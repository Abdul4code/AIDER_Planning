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
