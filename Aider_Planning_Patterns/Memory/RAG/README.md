# RAG-Based Memory Augmented Planning

This folder implements **Retrieval Augmented Generation (RAG)** based memory enhancement for Aider benchmark planning.

## Approach

Based on research from Lewis et al., 2020; Mao et al., 2020; Cai et al., 2022:

1. **Memory Storage**: Store experiences from past tasks (task descriptions + successful solutions) as text embeddings
2. **Retrieval**: For each new task, retrieve K most similar past experiences using vector similarity
3. **Augmentation**: Augment task instructions with relevant retrieved examples
4. **Execution**: Run task with enriched instructions

## Implementation Details

- **Memory Pool**: JSONL file storing task descriptions, solutions, and test outcomes
- **Vector Index**: FAISS-based vector similarity search for fast retrieval (using Ollama embeddings)
- **Retrieval Strategy**: Top-K (K=3) retrieved by cosine similarity of task descriptions
- **Prompt Augmentation**: Prepend retrieved examples to base instructions

## Key Components

- `scripts/rag_harness.py`: Main harness implementing RAG logic
- Memory management: Load/save memory pool, update with new experiences
- Integration points: Compatible with Aider Coder API (same as baseline/multiplan)

## Run

```bash
bash shared/scripts/run_rag.sh
```

Results saved to `RAG/results/`.

## Operationalization: RAG-Based Memory Augmentation Pattern

**Purpose:** Augment task instructions with relevant past experiences retrieved from a memory bank.

**Academic Formula:** $p = \text{plan}(E, g + \text{retrieve}(M, g); \Theta, P)$ where $\text{retrieve}(M, g) = \{\text{topK}(M, \text{sim}(g, m_i))\}_{i=1}^{K}$

### Model Configuration
- **Mode:** AIDER single-model code mode (NOT architect/editor dual-model)
- **Models used:** 1 (e.g., `ollama/qwen2.5-coder:7b-instruct`) + 1 embedding model (e.g., `ollama/nomic-embed-text`)
- **Architect role:** N/A (not used)
- **Editor role:** N/A (not used)
- **Rationale:** Single code generation model; embedding model is separate symbolic/retrieval component, not planning

### Prompt Strategy

**Single prompt with augmented context:**

#### Phase 1: Task Instructions (with RAG augmentation)
```
AUGMENTED_INSTRUCTIONS:
"## Similar Solutions from Past Experience

The following tasks were similar to yours. Consider their solutions:

[RETRIEVED EXAMPLE 1]
Problem: <description>
Solution approach: <code_summary>
Tests: <test_outcomes>

[RETRIEVED EXAMPLE 2]
...

## Your Task

<original_task_description>

Use the above experience to guide your solution.
"
```

**Critical design:**
- RAG augmentation is **pure prompt prepending** (no extra planning)
- Embedding model is separate (offline retrieval)
- Single model invocation (same as baseline, just augmented prompt)
- Retrieval happens before planning (top-K by vector similarity)

### Model Invocation Pattern

```
Phase 1: Retrieve (offline, no LLM call)
  [FAISS vector search: query=task_description]
  → Top-K=3 most similar past tasks
  → Extract: problem + solution + test results
  → Format as augmentation text

Phase 2: Augment prompt
  augmented_prompt = AUGMENTED_INSTRUCTIONS + retrieved_examples + task_description

Phase 3: Plan (same as baseline, just with richer context)
  [coder.run(with_message=augmented_prompt)]
  → Generate code solution
  → Run unit tests
  → PASS? → Done
  → FAIL? → Retry with error feedback (same as baseline)
```

**Invocation breakdown per task:**
- Retrieval call: 0 (pure vector search, no LLM)
- Planning call: 1 (same as baseline, augmented prompt)
- Total: 1 call (same invocation count as baseline)

### Orchestration Logic

```python
# Pre-execution: Build memory index (one-time)
memory_pool = load_memory_pool("memory.jsonl")
embedding_model = OllamaEmbeddings(model="nomic-embed-text")
faiss_index = build_faiss_index(memory_pool, embedding_model)

# Per-task execution
for task in tasks:
    # Phase 1: Retrieve past experiences (symbolic, no LLM)
    task_embedding = embedding_model.encode(task.description)
    retrieval_results = faiss_index.search(task_embedding, k=3)
    
    retrieved_examples = [
        {
            "problem": memory_pool[idx].task_description,
            "solution": memory_pool[idx].solution_code,
            "outcomes": memory_pool[idx].test_outcomes
        }
        for idx, score in retrieval_results
    ]
    
    # Phase 2: Build augmented instructions
    augmentation_text = format_retrieved_examples(retrieved_examples)
    current_instructions = augmentation_text + "\n\n" + task.description
    
    # Phase 3: Execute (standard baseline loop with augmented prompt)
    for attempt in range(tries):
        response = coder.run(with_message=current_instructions)
        errors = run_unit_tests(...)
        
        if errors:
            current_instructions = errors + TEST_FAILURES.format(...)
        else:
            break
    
    # Phase 4: Update memory (optional, for next run)
    if test_pass:
        memory_pool.append({
            "task_description": task.description,
            "solution_code": response,
            "test_outcomes": "pass"
        })
```

### Relationship to AIDER's Native Architecture

**Does NOT use architect/editor mode.**

- Architect/editor is not needed; RAG is pure prompt augmentation
- No additional planning phases; execution is identical to baseline
- Memory retrieval is symbolic (vector search), not model-based
- Single model invocation ensures comparability with baseline

### Token and Energy Implications

- **Tokens per task:** 1.0-1.3× baseline
  - Base code generation: 1× (same as baseline)
  - Augmented prompt tokens: +10-30% (depends on retrieved example length)
  - Test retry tokens: same as baseline
- **Model invocations:** 1 per task (same as baseline)
- **Retrieval cost:** O(1) FAISS search + embedding (no LLM)
- **Time per task:** 1.05-1.15× baseline (embedding search is milliseconds)
- **Energy:** ~1.1× baseline (retrieval negligible, augmented prompt tokens minor)
- **Reasoning overhead:** None (no reasoning models; pure retrieval)

### Reproducibility Checklist

- [ ] Single code generation model specified in `.env`
- [ ] Separate embedding model specified (`AIDER_EMBEDDING_MODEL`)
- [ ] No architect/editor configuration
- [ ] FAISS index built from memory pool before execution
- [ ] Top-K retrieval set to K=3 (hardcoded or configurable via env)
- [ ] Retrieved examples formatted consistently (problem + solution + outcomes)
- [ ] Augmented prompt prepends examples before task description
- [ ] Single coder.run() call per task (same as baseline)
- [ ] Memory pool persistence documented (saved between runs)
- [ ] Memory pool seeding strategy documented (initial examples, or learned)
- [ ] Per-task timeout enforced (900s = 15 min max)
- [ ] CodeCarbon tracks only code generation calls (retrieval excluded)
- [ ] Embedding model version pinned (e.g., nomic-embed-text:latest)
