---
name: nuke-machine-learning
description: >-
  Host skill - inspect and build Nuke AIR machine learning workflows with
  CopyCat, BigCat beta, Inference, Deblur, Upscale and CatFileCreator. Convert
  explicitly selected trusted TorchScript models and run bounded CPU training
  through shared async jobs while preserving the live graph.
license: MIT
compatibility: "Nuke AIR; native contracts qualified on NukeX 16.1; Python 3.9+; dcc-mcp-core 0.20+"
allowed-tools: Python
metadata:
  dcc-mcp:
    dcc: nuke
    version: "0.0.0"
    layer: domain
    stage: scene
    search-hint: "nuke AIR machine learning CopyCat BigCat Inference Deblur Upscale CatFileCreator TorchScript cat pt training model conversion"
    tags: "nuke, air, machine-learning, copycat, bigcat, inference, torchscript"
    tools: tools.yaml
    skill-reference-docs:
      - "references/*.md"
---

# AIR machine learning

Inspect the environment first. A discovered class or model file does not prove
license availability or execution. Inspection never creates nodes or loads models.
CopyCat, BigCat and CatFileCreator require a genuine NukeX or Studio host;
BigCat requires 16.1+ and remains a Beta feature; CatFileCreator requires 13.1+.
Other operations check their
native class and knob contracts. This skill stays discoverable in plain Nuke.

`create_ml_node` adds one exact AIR node, explicit image/ground-truth or mask
connections, and allowlisted static settings. It preserves outside nodes and
selection, resolves name collisions, defaults to CPU, and starts no computation.
Use ordinary node graph tools for later inspection and connections. Render
Inference, Deblur and Upscale through `nuke-preview` or an existing Write node.

Training and conversion execute only their named operation in a matching owned
NukeX child, with isolated temporary/profile directories and no GPU workload.
Each has an explicit 1–120 second wall-clock limit. The current training node must
be configured for CPU and 1–1000 epochs. The shared Core async job is the sole
job/cancellation authority; use `jobs_get_status` and `jobs_cancel`, or CLI
`call --wait`. Do not equate queued/started with completed: require the final
result and nonempty `.cat` artifact. Partial models and logs are retained on
failure, cancellation or timeout. Native validation/snapshot stages dispatch to
the host main thread; the owned process is monitored on the shared job worker.

`convert_torchscript_model` requires a caller-selected trusted `.pt` in an
existing CatFileCreator node. Conversion executes model code. It never downloads
models, imports a training framework, or overwrites an existing output. Output
is a `.cat` file; create an Inference node explicitly when wanted. Custom model
knobs, distributed training, resume, perceptual-model downloads and GPU training
are outside this bounded surface.

Native scripting identifiers differ from some 16.1 user-guide labels. The
implementation uses actual native knobs: CatFileCreator `torchScriptFile`,
`catFile`, `channelsIn`, `channelsOut`, `modelId`, `outputScale`; Inference
`halfPrecision`; trainers `useMultiResolution`. Missing knobs fail explicitly.
See the [workflow and primary references](references/air-workflows.md).
