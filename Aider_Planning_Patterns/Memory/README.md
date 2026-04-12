# Memory-Augmented Planning

This folder contains **memory augmentation** variants for the Aider benchmark runner.

## Implemented Variants

### RAG (Retrieval Augmented Generation)

**Location**: `Memory/RAG/`

Implements RAG-based memory augmentation following Lewis et al., 2020; Mao et al., 2020; Cai et al., 2022.

**Approach**:
- Stores successful task solutions and task descriptions as embeddings in a memory pool
- For each new task, retrieves K=2-3 most similar past solutions using vector similarity
- Augments task instructions with retrieved examples before execution
- Updates memory pool with new successful solutions progressively

**Key Features**:
- FAISS-based vector retrieval (Ollama embeddings)
- Cosine similarity ranking with embedding caching
- Progressive memory building across runs
- Adaptive retrieval (K scales with pool size)
- Graceful error handling with fallback to baseline

**Performance**:
- Baseline: 6/10 passing (60%)
- RAG: 6/10 passing (60%) - **matches baseline exactly** ✅
- All tasks complete < 15 min (max: 121.6s)

**Run**:
```bash
bash shared/scripts/run_rag.sh
```

**Results**: `Memory/RAG/results/`

**Documentation**: 
- `Memory/RAG/README.md` - Technical overview
- `Memory/RAG/IMPLEMENTATION_REPORT.md` - Detailed implementation report

## Future Ideas

- Embodied memory (LoRA fine-tuning on successful patterns)
- Hierarchical memory with task clustering
- Forgetting mechanisms for stale memories
- Multi-level memory architecture (like MemGPT)
