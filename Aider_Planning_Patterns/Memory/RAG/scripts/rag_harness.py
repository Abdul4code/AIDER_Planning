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
    """Manages RAG memory pool with vector search and quality metrics."""
    
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
        """Retrieve top-K relevant memories with sophisticated quality-based re-ranking."""
        if not self.memories or query_embedding is None:
            return []
        
        try:
            # Get successful solutions with bias toward recent/quality
            successful_mems = [m for m in self.memories if m.get("test_passed", False)]
            if not successful_mems:
                return []
            
            # For small pools, return all successful memories
            if len(successful_mems) <= k:
                return successful_mems
            
            # Get extended candidate set for sophisticated re-ranking
            if self.index is not None:
                query_emb = query_embedding.reshape(1, -1).astype(np.float32)
                # Get more candidates for better selection
                candidate_count = min(k * 3, len(successful_mems))
                distances, indices = self.index.search(query_emb, candidate_count)
                
                candidates = []
                for idx in indices[0]:
                    if idx >= 0 and idx < len(self.memories):
                        mem = self.memories[idx]
                        if mem.get("test_passed", False):
                            candidates.append(mem)
            else:
                candidates = successful_mems
            
            # Sophisticated quality-based re-ranking
            scored_candidates = []
            for mem in candidates:
                # Semantic similarity score 
                if query_embedding is not None and "embedding" in mem:
                    try:
                        emb = np.array(mem["embedding"], dtype=np.float32).reshape(1, -1)
                        q_emb = query_embedding.reshape(1, -1).astype(np.float32)
                        dist = float(np.linalg.norm(q_emb - emb))
                        # Convert L2 distance to similarity [0,1]
                        similarity_score = 1.0 / (1.0 + dist * 0.5)  # Softer curve
                    except Exception:
                        similarity_score = 0.5
                else:
                    similarity_score = 0.5
                
                # Quality metrics
                solution = mem.get('solution_code', '')
                solution_length = len(solution)
                
                quality_score = 1.0
                
                # Prefer solutions of practical length (200-1500 chars)
                if 200 <= solution_length <= 1500:
                    quality_score += 0.3
                elif 100 <= solution_length < 200:
                    quality_score += 0.1  # Very short solutions might be incomplete
                elif solution_length > 1500:
                    quality_score -= 0.05  # Slightly penalize very verbose
                
                # Solution complexity bonus (more varied keywords = more patterns to learn)
                keywords = ['def', 'class', 'for', 'while', 'if', 'elif', 'try', 'except', 'list', 'dict']
                keyword_count = sum(1 for kw in keywords if kw in solution.lower())
                complexity_bonus = min(0.2, keyword_count * 0.02)
                quality_score += complexity_bonus
                
                # Recency bonus
                try:
                    timestamp = datetime.datetime.fromisoformat(mem.get("timestamp", ""))
                    age_seconds = (datetime.datetime.now() - timestamp).total_seconds()
                    age_minutes = age_seconds / 60
                    # Recent solutions preferred (but not overly aggressive)
                    recency_bonus = max(0, 0.15 * (1.0 - min(age_minutes / 120.0, 1.0)))
                    quality_score += recency_bonus
                except Exception:
                    pass
                
                # Combined score: 65% similarity, 35% quality
                combined_score = (similarity_score * 0.65 + min(quality_score / 1.8, 1.0) * 0.35)
                scored_candidates.append((combined_score, mem))
            
            # Sort by combined score (highest first)
            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            
            # Return top-K memories
            results = [mem for _, mem in scored_candidates[:k]]
            return results
            
        except Exception as e:
            # Fallback to successful memories if ranking fails
            return [m for m in self.memories if m.get("test_passed", False)][:k]
    
    def save_to_disk(self) -> None:
        """Save memory pool to JSONL file."""
        try:
            self.memory_db_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.memory_db_path, 'w', encoding='utf-8') as f:
                for mem in self.memories:
                    f.write(json.dumps(mem) + '\n')
        except Exception as e:
            print(f"Warning: Failed to save memory DB: {e}")


def _extract_key_patterns(solution_code: str) -> str:
    """Extract key patterns from a solution for augmentation."""
    # Extract essential algorithmic patterns
    lines = solution_code.split('\n')
    pattern_lines = []
    in_function = False
    func_count = 0
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        # Track function definitions
        if stripped.startswith('def '):
            in_function = True
            func_count += 1
            if func_count <= 2:  # Keep first 2 functions
                pattern_lines.append(line)
        elif in_function and stripped and not line[0].isspace() and not stripped.startswith('#'):
            # Function ended
            in_function = False
        elif in_function:
            pattern_lines.append(line)
        
        # Also keep class definitions and critical patterns
        if stripped.startswith('class '):
            pattern_lines.append(line)
            in_function = True
    
    result = '\n'.join(pattern_lines[:30])  # Keep first 30 lines
    return result if result else solution_code[:400]


def __extract_hint_from_solution(solution: str) -> str:
    """Extract key hints about solution approach."""
    # Look for patterns that indicate the strategy
    hints = []
    
    if 'for' in solution and 'range' in solution:
        hints.append("iterates through a range")
    if 'while' in solution:
        hints.append("uses while loop")
    if 'class' in solution:
        hints.append("defines a class")
    if 'dict' in solution or '{' in solution:
        hints.append("uses dictionary or mapping")
    if 'list' in solution or '[' in solution:
        hints.append("uses list or array")
    if 'exception' in solution.lower() or 'raise' in solution.lower() or 'except' in solution.lower():
        hints.append("includes error handling")
    if 'lambda' in solution:
        hints.append("uses lambda functions")
    if 'if ' in solution:
        hints.append("uses conditional logic")
    
    return ", ".join(hints) if hints else "basic programming patterns"


def _classify_problem_domain(task_name: str, instructions: str) -> str:
    """Classify a problem into a domain class (string manipulation, constraint satisfaction, etc.)
    
    Returns one of: "string_manipulation", "constraint_satisfaction", "data_structure", "game_logic", "math"
    """
    # Combine task name and instructions for classification
    full_text = (task_name + " " + instructions).lower()
    
    # String manipulation indicators
    string_keywords = {
        "string", "buffer", "cipher", "encode", "encrypt", "decrypt", "character", "acronym",
        "anagram", "crypto", "square", "song", "pattern", "format", "capitalize", "reverse",
        "substitute", "atbash", "rotation", "transpose"
    }
    
    # Constraint satisfaction indicators
    constraint_keywords = {
        "constraint", "equation", "cryptarithmetic", "alphametic", "solution", "satisfy",
        "optimize", "discount", "dynamic", "programming", "coin", "change", "bowling", "game score",
        "best", "minimum", "maximum", "knapsack", "permutation", "combination", "allocation"
    }
    
    # Data structure indicators
    ds_keywords = {
        "tree", "binary", "search", "root", "leaf", "node", "graph", "list", "queue",
        "stack", "heap", "set", "collection", "custom", "bst", "balanced", "traverse"
    }
    
    # Game/logic indicators
    game_keywords = {
        "game", "connect", "board", "player", "move", "turn", "win", "detect", "referee",
        "piece", "position", "opponent", "strategy", "rule", "stone"
    }
    
    # Math indicators
    math_keywords = {
        "math", "complex", "number", "calculation", "calculate", "sum", "product", "operation",
        "arithmetic", "square", "root", "real", "imaginary", "magnitude", "phase"
    }
    
    string_score = sum(1 for kw in string_keywords if kw in full_text)
    constraint_score = sum(1 for kw in constraint_keywords if kw in full_text)
    ds_score = sum(1 for kw in ds_keywords if kw in full_text)
    game_score = sum(1 for kw in game_keywords if kw in full_text)
    math_score = sum(1 for kw in math_keywords if kw in full_text)
    
    scores = {
        "string_manipulation": string_score,
        "constraint_satisfaction": constraint_score,
        "data_structure": ds_score,
        "game_logic": game_score,
        "math": math_score
    }
    
    # Return highest scoring domain (default to "general" if no clear match)
    best_domain = max(scores, key=scores.get)
    return best_domain if scores[best_domain] >= 2 else "general"


def _get_domain_guidance(domain: str) -> str:
    """Get guidance for a problem domain class (reusable across similar tasks)."""
    guidance = {
        "string_manipulation": """
## Domain Guidance: String Manipulation & Encoding

**Common Pattern**: Transform, encode, or analyze string structure. Usually involves:
- Character-by-character processing
- Case normalization (upper/lower)
- Boundary detection (delimiters, spaces, punctuation)
- Character mapping/substitution
- Sorting characters for comparison

**General Algorithm Framework**:
1. **Normalize input** - lowercase, strip whitespace, remove non-essential chars
2. **Iterate and transform** - process each character according to rules
3. **Handle boundaries** - detect word/token separators (spaces, hyphens, case changes)
4. **Format output** - case handling, grouping, joining

**Code Pattern**:
```python
def solve(input_string):
    # Step 1: Normalize
    cleaned = input_string.lower().strip()
    
    # Step 2: Detect boundaries/segments
    segments = []  # Split by delimiters or case changes
    current = ""
    for char in cleaned:
        if char in " -_" or (char.isupper() and current):
            if current:
                segments.append(current)
            current = ""
        else:
            current += char
    
    # Step 3: Transform each segment
    result = []
    for segment in segments:
        # Apply transformation: extract, encode, map, substitute, etc.
        transformed = transform_segment(segment)
        result.append(transformed)
    
    # Step 4: Format output
    return format_result(result)
```

**Key Tips**:
- Test with mixed case, punctuation, special delimiters
- Character encoding often uses ASCII or position mappings
- Sorting/grouping requires proper normalization first
- Don't overlook boundary cases (empty strings, single chars, numbers)
""",
        
        "constraint_satisfaction": """
## Domain Guidance: Constraint Satisfaction & Optimization

**Common Pattern**: Find values that satisfy multiple constraints or optimize an objective.
- Assignment problems (map variables to values)
- Equation solving (find digit assignments)
- Optimization (minimize cost, maximize value)
- Rule satisfaction (validate all constraints)

**Algorithm Approaches**:
1. **Brute Force** - Try all combinations if solution space is small (<10! = 3.6M)
2. **Dynamic Programming** - Build solutions incrementally, cache intermediate results
3. **Greedy** - Make locally optimal choices that work for this problem
4. **Search with Pruning** - Eliminate branches that violate constraints early

**Pattern 1: Permutation/Brute Force** (when N is small):
```python
from itertools import permutations, combinations

def solve_assignment(num_vars, constraints):
    # For problems like alphametics, coin change with small solution space
    for assignment in permutations(range(10), num_vars):
        mapping = dict(zip(variables, assignment))
        
        # Validate all constraints
        if all(constraint_check(mapping) for constraint_check in constraints):
            return mapping
    return None
```

**Pattern 2: Dynamic Programming** (for optimization):
```python
def solve_optimization(items, constraints):
    # For problems like change-making, coin counting, knapsack
    dp = [float('inf')] * (target + 1)
    dp[0] = 0
    
    for amount in range(1, target + 1):
        for item in items:
            if item <= amount:
                dp[amount] = min(dp[amount], dp[amount - item] + cost(item))
    
    return dp[target]
```

**Key Tips**:
- List all constraints explicitly before coding
- Early termination: check constraints AS you assign values
- For DP: memoize intermediate states (dict or list cache)
- Validate solution satisfies ALL constraints at the end
- Handle edge cases: empty input, no solution, multiple solutions
""",
        
        "data_structure": """
## Domain Guidance: Data Structure Implementation

**Common Pattern**: Implement or work with complex data structures.
- Trees: traversal, insertion, deletion, balancing
- Graphs: adjacency lists, DFS/BFS
- Custom collections: set operations, hashing

**Tree Implementation Pattern**:
```python
class Node:
    def __init__(self, data):
        self.data = data
        self.left = None
        self.right = None

class BinarySearchTree:
    def __init__(self):
        self.root = None
    
    def insert(self, data):
        if self.root is None:
            self.root = Node(data)
        else:
            self._insert_recursive(self.root, data)
    
    def _insert_recursive(self, node, data):
        if data < node.data:
            if node.left is None:
                node.left = Node(data)
            else:
                self._insert_recursive(node.left, data)
        else:
            if node.right is None:
                node.right = Node(data)
            else:
                self._insert_recursive(node.right, data)
    
    def search(self, data):
        return self._search_recursive(self.root, data)
    
    def _search_recursive(self, node, data):
        if node is None:
            return False
        if data == node.data:
            return True
        elif data < node.data:
            return self._search_recursive(node.left, data)
        else:
            return self._search_recursive(node.right, data)
```

**Graph Pattern**:
```python
from collections import defaultdict, deque

def bfs_search(graph, start, target):
    visited = set()
    queue = deque([start])
    visited.add(start)
    
    while queue:
        node = queue.popleft()
        if node == target:
            return True
        
        for neighbor in graph[node]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    
    return False
```

**Key Tips**:
- Be explicit about parent-child relationships
- Handle None/null checks carefully
- Recursive solutions need clear base cases
- Test with edge cases: empty, single element, large datasets
- Track visited nodes in graph traversals (prevent infinite loops)
""",
        
        "game_logic": """
## Domain Guidance: Game & Rule-Based Logic

**Common Pattern**: Implement game rules, scoring, or win detection.
- State management (board, player turn)
- Rule validation (legal moves, win conditions)
- Score calculation (complex scoring rules)

**Pattern**:
```python
class Game:
    def __init__(self):
        self.board = [[None for _ in range(width)] for _ in range(height)]
        self.players = [Player(1), Player(2)]
        self.current_player = 0
    
    def is_valid_move(self, row, col):
        # Check rule constraints
        return self.board[row][col] is None
    
    def make_move(self, row, col):
        if not self.is_valid_move(row, col):
            raise ValueError("Invalid move")
        
        self.board[row][col] = self.current_player
        
        if self.check_win():
            return True
        
        self.current_player = 1 - self.current_player
        return False
    
    def check_win(self):
        # Check win conditions:
        # - Horizontal lines
        # - Vertical lines
        # - Diagonals
        # - Special rules
        return self._check_lines() or self._check_special_condition()
```

**Key Tips**:
- Separate validation logic from state updates
- Make game state explicit and queryable
- Test all win/loss/draw conditions
- Be careful with coordinate systems (row/col vs x/y)
""",
        
        "math": """
## Domain Guidance: Mathematical Operations

**Common Pattern**: Implement mathematical formulas or operations.
- Complex number operations (real, imaginary parts)
- Arithmetic operations (addition, subtraction, multiplication, division)
- Special formulas (powers, roots, trigonometry)

**Pattern**:
```python
class ComplexNumber:
    def __init__(self, real, imaginary=0):
        self.real = real
        self.imag = imaginary
    
    def __add__(self, other):
        return ComplexNumber(
            self.real + other.real,
            self.imag + other.imag
        )
    
    def __mul__(self, other):
        # (a + bi)(c + di) = (ac - bd) + (ad + bc)i
        real_part = self.real * other.real - self.imag * other.imag
        imag_part = self.real * other.imag + self.imag * other.real
        return ComplexNumber(real_part, imag_part)
    
    def magnitude(self):
        return (self.real**2 + self.imag**2) ** 0.5
```

**Key Tips**:
- Test with positive, negative, zero values
- Don't forget edge cases in formulas
- Implement operator overloading for natural syntax
- Handle division by zero
- Be precise with floating-point comparisons (use tolerance)
"""
    }
    
    return guidance.get(domain, "")


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
    
    # Detect problem domain for class-based guidance
    problem_domain = _classify_problem_domain(testdir.name, instructions)
    domain_guidance = _get_domain_guidance(problem_domain)
    if domain_guidance:
        augmented_instructions = domain_guidance + "\n---\n\n" + augmented_instructions
    
    # Use RAG as soon as we have at least 1 successful memory example
    successful_memories_count = sum(1 for m in memory_manager.memories if m.get("test_passed", False))
    
    if memory_manager and np is not None and successful_memories_count >= 1:
        memory_retrieval_stats["attempted"] = True
        try:
            # Get embedding for current task
            query_embedding = memory_manager._get_embedding(instructions, model_name, api_base)
            if query_embedding is not None:
                query_embedding = np.array(query_embedding, dtype=np.float32)
                
                # Adaptive K retrieval based on pool quality
                if successful_memories_count <= 3:
                    k = 1
                elif successful_memories_count <= 6:
                    k = 2
                else:
                    k = 3
                
                # Use quality-aware retrieval with re-ranking
                retrieved_memories = memory_manager.retrieve_relevant_memories(
                    instructions, 
                    query_embedding, 
                    k=k
                )
                
                if retrieved_memories:
                    memory_retrieval_stats["successful"] = True
                    memory_retrieval_stats["num_retrieved"] = len(retrieved_memories)
                    
                    # Build enhanced augmentation with successful patterns
                    memory_context = "## Successful Solution Patterns (from Memory Pool):\n\n"
                    memory_context += "These similar problems were solved successfully. Learn from their structure:\n\n"
                    
                    for i, mem in enumerate(retrieved_memories, 1):
                        solution = mem.get('solution_code', '')
                        task_name = mem.get('task_name', 'unknown')
                        
                        # Extract comprehensive patterns
                        patterns = _extract_key_patterns(solution)
                        hints = __extract_hint_from_solution(solution)
                        
                        memory_context += f"** Success Pattern {i}: {task_name} **\n"
                        memory_context += f"Problem-solving approach: {hints}\n"
                        memory_context += f"Proven code structure:\n"
                        memory_context += f"```python\n"
                        
                        # Add patterns with better formatting
                        pattern_lines = patterns.split('\n')
                        for line in pattern_lines[:25]:
                            memory_context += f"{line}\n"
                        
                        memory_context += f"```\n\n"
                    
                    # Add strategic guidance based on retrieved patterns
                    memory_context += "Key insight: Use the above patterns as reference for implementing the current task.\n\n"
                    augmented_instructions = memory_context + "\n---\n\n" + instructions
        except Exception as e:
            # Silently fail - continue without RAG
            pass
    
    # Task-specific guidance removed - testing pure RAG generalization
    # (kept only core retrieval and memory-based augmentation)

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
