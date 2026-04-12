#!/usr/bin/env python3
"""RAG-based memory augmented planning harness.

Implements Retrieval Augmented Generation for planning enhancement:
1. Store experiences (task + solution) in memory pool
2. Retrieve relevant past experiences for new tasks
3. Augment instructions with retrieved examples
4. Execute task with augmented instructions
5. Store results back to memory

Based on: Lewis et al., 2020; Mao et al., 2020; Cai et al., 2022
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional
import hashlib

try:
    from codecarbon import EmissionsTracker
except Exception:
    EmissionsTracker = None

try:
    import numpy as np
    import faiss
except Exception:
    np = None
    faiss = None

from aider import models, sendchat
from aider.coders import Coder, base_coder
from aider.io import InputOutput

INSTRUCTIONS_ADDENDUM = """
####

Use the above instructions to modify the supplied files: {file_list}
Don't change the names of existing functions or classes, as they may be referenced from other code like unit tests, etc.
Only use standard libraries, don't suggest installing any packages.
"""

TEST_FAILURES = """
####

See the testing errors above.
The tests are correct, don't try and change them.
Fix the code in {file_list} to resolve the errors.
"""


class RAGMemoryManager:
    """Manages RAG memory pool with vector search capabilities."""
    
    def __init__(self, memory_db_path: Path, embedding_model: str = "nomic-embed-text"):
        self.memory_db_path = memory_db_path
        self.embedding_model = embedding_model
        self.memories: list[dict[str, Any]] = []
        self.embeddings: Optional[np.ndarray] = None
        self.index: Optional[Any] = None
        self.model_name = None
        self._embedding_cache: dict[str, np.ndarray] = {}
        
        # Load existing memories
        if memory_db_path.exists():
            self._load_from_disk()
    
    def _get_embedding(self, text: str, model_name: str, api_base: str) -> Optional[np.ndarray]:
        """Get embedding vector from Ollama with caching."""
        try:
            # Try to use cached embedding if available
            text_hash = hashlib.md5(text[:500].encode()).hexdigest()
            if not hasattr(self, '_embedding_cache'):
                self._embedding_cache = {}
            
            if text_hash in self._embedding_cache:
                return self._embedding_cache[text_hash]
            
            import urllib.request
            import json as json_lib
            
            url = f"{api_base.rstrip('/')}/api/embed"
            # Use first 1000 chars for embedding (trade-off between relevance and speed)
            truncated_text = text[:1000]
            data = json_lib.dumps({
                "model": self.embedding_model,
                "input": truncated_text
            }).encode('utf-8')
            
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as response:  # Reduced timeout
                result = json_lib.loads(response.read().decode('utf-8'))
                if "embeddings" in result and len(result["embeddings"]) > 0:
                    emb = np.array(result["embeddings"][0], dtype=np.float32)
                    self._embedding_cache[text_hash] = emb
                    return emb
        except Exception as e:
            # Silently fail - we'll just skip RAG for this task
            pass
        return None
    
    def _load_from_disk(self) -> None:
        """Load memory pool from JSONL file."""
        if not self.memory_db_path.exists():
            return
        
        self.memories = []
        try:
            with open(self.memory_db_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        self.memories.append(json.loads(line))
        except Exception as e:
            print(f"Warning: Failed to load memory DB: {e}")
    
    def _build_index(self) -> None:
        """Build FAISS index from memory embeddings."""
        if not self.memories or faiss is None or np is None:
            return
        
        # Rebuild embeddings list
        embeddings_list = []
        for mem in self.memories:
            if "embedding" in mem:
                try:
                    emb = np.array(mem["embedding"], dtype=np.float32)
                    embeddings_list.append(emb)
                except Exception:
                    pass
        
        if embeddings_list:
            self.embeddings = np.array(embeddings_list, dtype=np.float32)
            dim = self.embeddings.shape[1]
            self.index = faiss.IndexFlatL2(dim)
            self.index.add(self.embeddings)
    
    def add_memory(
        self, 
        task_name: str, 
        task_description: str, 
        solution_code: str, 
        test_passed: bool,
        embedding: Optional[list[float]] = None
    ) -> None:
        """Add a memory to the pool."""
        memory_entry = {
            "task_name": task_name,
            "task_description": task_description,
            "solution_code": solution_code,
            "test_passed": test_passed,
            "timestamp": datetime.datetime.now().isoformat(),
            "embedding": embedding or []
        }
        self.memories.append(memory_entry)
        self._build_index()
    
    def retrieve_relevant_memories(
        self, 
        query_text: str, 
        query_embedding: Optional[np.ndarray] = None,
        k: int = 3
    ) -> list[dict[str, Any]]:
        """Retrieve top-K relevant memories."""
        if not self.memories or self.index is None or query_embedding is None:
            return []
        
        try:
            # Only search among successful solutions
            valid_indices = [i for i, m in enumerate(self.memories) if m.get("test_passed", False)]
            if not valid_indices:
                return []
            
            query_emb = query_embedding.reshape(1, -1).astype(np.float32)
            distances, indices = self.index.search(query_emb, min(k, len(valid_indices)))
            
            results = []
            for idx in indices[0]:
                if idx >= 0 and idx < len(self.memories):
                    results.append(self.memories[idx])
            return results[:k]
        except Exception as e:
            print(f"Warning: Memory retrieval failed: {e}")
            return []
    
    def save_to_disk(self) -> None:
        """Save memory pool to JSONL file."""
        try:
            self.memory_db_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.memory_db_path, 'w', encoding='utf-8') as f:
                for mem in self.memories:
                    f.write(json.dumps(mem) + '\n')
        except Exception as e:
            print(f"Warning: Failed to save memory DB: {e}")


def _seconds_left(deadline_ts: Optional[float]) -> Optional[int]:
    if deadline_ts is None:
        return None
    return int(deadline_ts - datetime.datetime.now().timestamp())


def _effective_call_timeout(remaining_seconds: Optional[int], llm_timeout: Optional[int]) -> Optional[int]:
    if remaining_seconds is not None:
        if remaining_seconds <= 0:
            return None
        if llm_timeout and llm_timeout > 0:
            return max(1, min(remaining_seconds, llm_timeout))
        return max(1, remaining_seconds)
    return llm_timeout


def cleanup_test_output(output: str, testdir: Path) -> str:
    res = re.sub(r"\bin \d+\.\d+s\b", "", output)
    return res.replace(str(testdir), str(testdir.name))


def run_unit_tests(
    original_exercise_dir: Path,
    testdir: Path,
    history_fname: Path,
    test_files: list[str],
    timeout_seconds: Optional[int] = None,
) -> Optional[str]:
    timeout = 60 * 3
    if timeout_seconds is not None and timeout_seconds > 0:
        timeout = min(timeout, timeout_seconds)

    test_commands = {
        ".py": ["pytest"],
        ".rs": ["cargo", "test", "--", "--include-ignored"],
        ".go": ["go", "test", "./..."],
        ".js": ["/aider/benchmark/npm-test.sh"],
        ".cpp": ["/aider/benchmark/cpp-test.sh"],
        ".java": ["./gradlew", "test"],
    }

    extensions = {Path(f).suffix for f in test_files}
    command = None
    for ext in extensions:
        if ext in test_commands:
            command = test_commands[ext]
            break

    if not command:
        raise ValueError(f"No test command found for file extensions: {sorted(extensions)}")

    for file_path in test_files:
        src = original_exercise_dir / file_path
        dst = testdir / file_path
        if src.exists():
            os.makedirs(dst.parent, exist_ok=True)
            shutil.copy(src, dst)

    for file_path in test_files:
        if file_path.endswith(".java"):
            test_file = testdir / file_path
            if test_file.exists():
                content = test_file.read_text(encoding="utf-8", errors="replace")
                content = re.sub(r"@Disabled\([^)]*\)\s*\n", "", content)
                test_file.write_text(content, encoding="utf-8")

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        cwd=testdir,
        encoding="utf-8",
        errors="replace",
    )

    success = result.returncode == 0
    res = cleanup_test_output(result.stdout, testdir)

    with history_fname.open("a", encoding="utf-8") as fh:
        fh.write(f"```\n{res}\n```")

    if not success:
        return res

    return None


def build_task_list(
    exercises_dir: Path,
    languages: str,
    keywords: str,
    num_tests: int,
    shuffle_tasks: bool,
) -> list[Path]:
    lang_filter: set[str] = set()
    if languages.strip():
        lang_filter = {x.strip().lower() for x in languages.split(",") if x.strip()}

    keyword_filter: list[str] = []
    if keywords.strip():
        keyword_filter = [x.strip() for x in keywords.split(",") if x.strip()]

    tasks: list[Path] = []
    for lang_dir in sorted(exercises_dir.iterdir()):
        if not lang_dir.is_dir():
            continue
        if lang_filter and lang_dir.name.lower() not in lang_filter:
            continue

        practice = lang_dir / "exercises" / "practice"
        if not practice.is_dir():
            continue

        for ex_dir in sorted(practice.iterdir()):
            if not ex_dir.is_dir():
                continue
            rel = str(ex_dir.relative_to(exercises_dir))
            if keyword_filter and not any(k in rel for k in keyword_filter):
                continue
            tasks.append(ex_dir)

    if shuffle_tasks:
        random.shuffle(tasks)
    if num_tests > 0:
        tasks = tasks[:num_tests]

    return tasks


def get_commit_hash() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=7", "HEAD"], stderr=subprocess.DEVNULL, text=True
        )
        hsh = out.strip()
        return hsh or "unknown"
    except Exception:
        return "unknown"


def run_single_task_with_rag(
    original_exercise_dir: Path,
    testdir: Path,
    model_name: str,
    edit_format: str,
    tries: int,
    num_ctx: Optional[int],
    extra_instructions: str,
    commit_hash: str,
    llm_timeout: Optional[int],
    task_timeout_seconds: Optional[int],
    memory_manager: RAGMemoryManager,
    api_base: str,
) -> dict[str, Any]:
    tracker = None
    task_emissions_kg = None
    task_energy_kwh = None
    if EmissionsTracker is not None:
        tracker = EmissionsTracker(save_to_file=False, log_level="error")
        tracker.start()

    history_fname = testdir / ".aider.chat.history.md"
    results_fname = testdir / ".aider.results.json"
    if results_fname.exists():
        return json.loads(results_fname.read_text(encoding="utf-8"))

    config_file = testdir / ".meta" / "config.json"
    if not config_file.exists():
        raise FileNotFoundError(f"Missing config file: {config_file}")

    config = json.loads(config_file.read_text(encoding="utf-8"))
    test_files = config.get("files", {}).get("test", [])
    example_files = config.get("files", {}).get("example", [])
    solution_files = set(config.get("files", {}).get("solution", []))

    ignore_files = {"CMakeLists.txt", "Cargo.toml"}
    ignore_files.update(str(p.relative_to(testdir)) for p in testdir.glob(".meta/**/*") if p.is_file())
    ignore_files.update(str(p.relative_to(testdir)) for p in testdir.glob(".docs/**/*") if p.is_file())
    ignore_files.update(test_files)
    ignore_files.update(example_files)
    solution_files.difference_update(ignore_files)

    fnames: list[Path] = []
    for file_path in sorted(solution_files):
        src = testdir / file_path
        if src.exists() and src.is_file():
            fnames.append(src)

    file_list = " ".join(fname.name for fname in fnames)

    # Build base instructions
    instructions = ""
    intro = testdir / ".docs" / "introduction.md"
    if intro.exists():
        instructions += intro.read_text(encoding="utf-8", errors="replace")
    instructions += (testdir / ".docs" / "instructions.md").read_text(encoding="utf-8", errors="replace")
    append_path = testdir / ".docs" / "instructions.append.md"
    if append_path.exists():
        instructions += append_path.read_text(encoding="utf-8", errors="replace")

    instructions += INSTRUCTIONS_ADDENDUM.format(file_list=file_list)
    if extra_instructions:
        instructions += f"\n\n####\n\n{extra_instructions}\n"

    # RAG: Retrieve relevant memories (enable as soon as we have 1+ successful memory)
    augmented_instructions = instructions
    retrieved_memories = []
    memory_retrieval_stats = {"attempted": False, "successful": False, "num_retrieved": 0}
    
    # Use RAG as soon as we have at least 1 successful memory example
    successful_memories_count = sum(1 for m in memory_manager.memories if m.get("test_passed", False))
    
    if memory_manager and np is not None and successful_memories_count >= 1:
        memory_retrieval_stats["attempted"] = True
        try:
            # Get embedding for current task
            query_embedding = memory_manager._get_embedding(instructions, model_name, api_base)
            if query_embedding is not None:
                query_embedding = np.array(query_embedding, dtype=np.float32)
                # Retrieve top memories based on similarity
                # Start with 1-2 on early runs, scale up to 3 as pool grows
                k = min(2 + max(0, successful_memories_count // 3), 3)
                retrieved_memories = memory_manager.retrieve_relevant_memories(
                    instructions, 
                    query_embedding, 
                    k=k
                )
                
                if retrieved_memories:
                    memory_retrieval_stats["successful"] = True
                    memory_retrieval_stats["num_retrieved"] = len(retrieved_memories)
                    
                    # Create concise memory context - focus on solution patterns
                    memory_context = "## Past Solution Reference (from memory):\n\n"
                    for i, mem in enumerate(retrieved_memories, 1):
                        solution = mem.get('solution_code', '')[:150]
                        memory_context += f"{i}. {mem.get('task_name', 'task')}:\n"
                        memory_context += f"   ```python\n   {solution.replace(chr(10), chr(10) + '   ')}...\n   ```\n\n"
                    
                    augmented_instructions = memory_context + "\n---\n\n" + instructions
        except Exception as e:
            # Silently fail - continue without RAG
            pass

    io = InputOutput(pretty=False, yes=True, chat_history_file=history_fname)
    main_model = models.Model(model_name, weak_model=None, editor_model=None, editor_edit_format=None, verbose=False)

    if num_ctx:
        if not main_model.extra_params:
            main_model.extra_params = {}
        main_model.extra_params["num_ctx"] = num_ctx

    if llm_timeout and llm_timeout > 0:
        main_model.timeout = llm_timeout

    actual_edit_format = edit_format or main_model.edit_format
    coder = Coder.create(
        main_model,
        actual_edit_format,
        io,
        fnames=fnames,
        use_git=False,
        stream=False,
        verbose=False,
        cache_prompts=True,
        suggest_shell_commands=False,
        ignore_mentions=ignore_files,
    )
    coder.get_file_mentions = lambda _: set()

    timeouts = 0
    syntax_errors = 0
    indentation_errors = 0
    lazy_comments = 0

    duration = 0.0
    test_outcomes: list[bool] = []
    current_instructions = augmented_instructions
    task_timed_out = False
    task_deadline_ts: Optional[float] = None
    if task_timeout_seconds and task_timeout_seconds > 0:
        task_deadline_ts = datetime.datetime.now().timestamp() + task_timeout_seconds

    for _ in range(tries):
        remaining_seconds = _seconds_left(task_deadline_ts)
        if remaining_seconds is not None and remaining_seconds <= 0:
            task_timed_out = True
            break

        call_timeout = _effective_call_timeout(remaining_seconds, llm_timeout)
        if call_timeout is None:
            task_timed_out = True
            break
        main_model.timeout = call_timeout

        start = datetime.datetime.now().timestamp()

        response = coder.run(with_message=current_instructions, preproc=False)

        duration += datetime.datetime.now().timestamp() - start
        pattern = r"^[+]? *[#].* [.][.][.] "
        lazy_comments += len(re.findall(pattern, response or "", re.MULTILINE))

        if coder.last_keyboard_interrupt:
            raise KeyboardInterrupt

        try:
            remaining_seconds = _seconds_left(task_deadline_ts)
            if remaining_seconds is not None and remaining_seconds <= 0:
                errors = "Task timed out (overall task deadline exceeded)!"
                timeouts += 1
                task_timed_out = True
            else:
                errors = run_unit_tests(
                    original_exercise_dir,
                    testdir,
                    history_fname,
                    test_files,
                    timeout_seconds=remaining_seconds,
                )
        except subprocess.TimeoutExpired:
            errors = "Tests timed out!"
            timeouts += 1

        if errors:
            test_outcomes.append(False)
            err_lines = errors.splitlines()
            syntax_errors += sum(1 for line in err_lines if line.startswith("SyntaxError"))
            indentation_errors += sum(1 for line in err_lines if line.startswith("IndentationError"))

            current_instructions = errors
            current_instructions += TEST_FAILURES.format(file_list=file_list)
            if extra_instructions:
                current_instructions += f"\n\n####\n\n{extra_instructions}\n"

            if task_timed_out:
                break
        else:
            test_outcomes.append(True)
            break

    if not test_outcomes and task_timed_out:
        test_outcomes.append(False)

    # Update memory with this task's outcome
    if memory_manager and test_outcomes and test_outcomes[0]:
        try:
            # Collect solution code
            solution_code = ""
            for fname in fnames:
                try:
                    solution_code += f"# {fname.name}\n"
                    solution_code += fname.read_text(encoding="utf-8", errors="replace")
                    solution_code += "\n\n"
                except Exception:
                    pass
            
            # Get embedding for the task
            solution_embedding = memory_manager._get_embedding(instructions, model_name, api_base)
            embedding_list = solution_embedding.tolist() if solution_embedding is not None else []
            
            memory_manager.add_memory(
                task_name=testdir.name,
                task_description=instructions[:500],
                solution_code=solution_code[:1000],
                test_passed=test_outcomes[0],
                embedding=embedding_list
            )
        except Exception as e:
            print(f"Warning: Failed to update memory for {testdir.name}: {e}")

    if tracker is not None:
        try:
            emissions = tracker.stop()
            if emissions is not None:
                task_emissions_kg = float(emissions)
            final_data = getattr(tracker, "final_emissions_data", None)
            if final_data and getattr(final_data, "energy_consumed", None) is not None:
                task_energy_kwh = float(final_data.energy_consumed)
        except Exception:
            pass

    chat_hashes = list(
        zip(
            coder.chat_completion_call_hashes,
            coder.chat_completion_response_hashes,
        )
    )

    results = {
        "testdir": str(testdir),
        "testcase": testdir.name,
        "model": main_model.name,
        "edit_format": actual_edit_format,
        "tests_outcomes": test_outcomes,
        "cost": coder.total_cost,
        "duration": duration,
        "test_timeouts": timeouts,
        "commit_hash": commit_hash,
        "num_error_outputs": io.num_error_outputs,
        "num_user_asks": io.num_user_asks,
        "num_exhausted_context_windows": coder.num_exhausted_context_windows,
        "num_malformed_responses": coder.num_malformed_responses,
        "syntax_errors": syntax_errors,
        "indentation_errors": indentation_errors,
        "lazy_comments": lazy_comments,
        "reasoning_effort": None,
        "prompt_tokens": coder.total_tokens_sent,
        "completion_tokens": coder.total_tokens_received,
        "thinking_tokens": None,
        "task_timed_out": task_timed_out,
        "task_timeout_seconds": task_timeout_seconds,
        "codecarbon_emissions_kg": task_emissions_kg,
        "codecarbon_energy_kwh": task_energy_kwh,
        "arch_planning_enabled": True,
        "arch_decomp_mode": "rag-memory",
        "planner_calls": 0,
        "executor_calls": len(chat_hashes),
        "arch_plan_steps": 0,
        "arch_interleaved_cycles": 0,
        "chat_hashes": chat_hashes,
        "memory_retrieval": memory_retrieval_stats,
        "num_retrieved_memories": memory_retrieval_stats.get("num_retrieved", 0),
    }

    results_fname.write_text(json.dumps(results, indent=4) + "\n", encoding="utf-8")
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--edit-format", default="whole")
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--tries", type=int, default=2)
    ap.add_argument("--languages", default="")
    ap.add_argument("--keywords", default="")
    ap.add_argument("--num-tests", type=int, default=-1)
    ap.add_argument("--num-ctx", type=int, default=0)
    ap.add_argument("--exercises-dir", default="polyglot-benchmark")
    ap.add_argument("--shuffle-tasks", type=int, default=1, choices=[0, 1])
    ap.add_argument("--api-base", default="http://127.0.0.1:11434")
    args = ap.parse_args()

    benchmark_root = Path(os.environ.get("AIDER_BENCHMARK_DIR", "/benchmarks"))
    exercises_root = benchmark_root / args.exercises_dir
    if not exercises_root.exists():
        raise FileNotFoundError(f"Exercises dir does not exist: {exercises_root}")

    inner_dir = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S") + f"--{args.run_name}"
    run_root = benchmark_root / inner_dir
    run_root.mkdir(parents=True, exist_ok=True)

    tasks = build_task_list(
        exercises_dir=exercises_root,
        languages=args.languages,
        keywords=args.keywords,
        num_tests=args.num_tests,
        shuffle_tasks=bool(args.shuffle_tasks),
    )
    if not tasks:
        raise RuntimeError("No exercises matched the provided filters")

    print(f"RAG harness run root: {run_root}")
    print(f"Selected tasks: {len(tasks)}")

    # Initialize RAG memory manager
    memory_db = run_root / "memory.jsonl"
    memory_manager = RAGMemoryManager(memory_db)
    print(f"Memory pool size: {len(memory_manager.memories)} experiences")

    commit_hash = get_commit_hash()

    task_timeout_seconds = 15 * 60
    task_timeout_raw = os.environ.get("AIDER_BENCH_TASK_TIMEOUT_SECONDS", "").strip()
    if task_timeout_raw:
        try:
            task_timeout_seconds = int(task_timeout_raw)
        except Exception:
            task_timeout_seconds = 15 * 60
    if task_timeout_seconds < 0:
        task_timeout_seconds = 0

    retry_timeout_raw = os.environ.get("AIDER_BENCH_RETRY_TIMEOUT", "").strip()
    if retry_timeout_raw:
        try:
            retry_timeout = int(retry_timeout_raw)
        except Exception:
            retry_timeout = 60
    else:
        retry_timeout = 60

    if task_timeout_seconds > 0:
        retry_timeout = min(retry_timeout, task_timeout_seconds)

    sendchat.RETRY_TIMEOUT = retry_timeout
    base_coder.RETRY_TIMEOUT = retry_timeout
    models.RETRY_TIMEOUT = retry_timeout

    llm_timeout = 0
    llm_timeout_raw = os.environ.get("AIDER_BENCH_LLM_TIMEOUT", "").strip()
    if llm_timeout_raw:
        try:
            llm_timeout = int(llm_timeout_raw)
        except Exception:
            llm_timeout = 0
    elif task_timeout_seconds > 0:
        llm_timeout = min(120, task_timeout_seconds)

    extra_instructions = os.environ.get("AIDER_BENCH_EXTRA_INSTRUCTIONS", "").strip()

    def work(ex_dir: Path) -> dict[str, Any]:
        rel = ex_dir.relative_to(exercises_root)
        testdir = run_root / rel
        if testdir.exists():
            shutil.rmtree(testdir)
        shutil.copytree(ex_dir, testdir)

        print(f"Running task: {rel}")
        return run_single_task_with_rag(
            original_exercise_dir=ex_dir,
            testdir=testdir,
            model_name=args.model,
            edit_format=args.edit_format,
            tries=args.tries,
            num_ctx=args.num_ctx or 0,
            extra_instructions=extra_instructions,
            commit_hash=commit_hash,
            llm_timeout=llm_timeout,
            task_timeout_seconds=task_timeout_seconds,
            memory_manager=memory_manager,
            api_base=args.api_base,
        )

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = [executor.submit(work, task) for task in tasks]
        results = []
        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
                print(f"  -> {result.get('testcase', '?')}: {'✓' if result['tests_outcomes'] and result['tests_outcomes'][0] else '✗'}")
            except Exception as e:
                print(f"ERROR: {e}")
                raise

    # Save memory manager to disk
    memory_manager.save_to_disk()
    print(f"Memory pool updated: {len(memory_manager.memories)} experiences")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
