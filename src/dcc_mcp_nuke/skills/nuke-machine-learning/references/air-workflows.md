# Native AIR workflow

Call `inspect_ml_environment`, then `create_ml_node`. Use exact existing node
names as `input_node` and optional `reference_node`: the latter is ground truth
for CopyCat/BigCat or a mask for Deblur. CatFileCreator has no image inputs.
Settings are class-specific native knob names, never arbitrary scripts/buttons.
Training nodes need `epochs`, `modelSize`, `cropSize`, `batchSizeType`,
`batchSize`, `checkpointInterval`, `imageInterval` and `useMultiResolution`
configured intentionally. The tool uses CPU and starts no work during creation.

For conversion, configure a CatFileCreator with absolute `torchScriptFile`,
`channelsIn`/`channelsOut` lists such as `["rgba.red","rgba.green","rgba.blue"]`,
nonempty `modelId` and `outputScale` `[1,1]`. Invoke conversion with a new `.cat`
output. Create an Inference explicitly using its `modelFile`; native channels
and custom model state are resolved by Nuke, not parsed by this adapter.

For bounded training, configure paired image inputs and a maximum of 1000 epochs,
then request a new output directory. The owned child uses the same executable,
NukeX interactive licensing and a temporary script snapshot. It creates real
checkpoints/contact sheets and retains its stdout/stderr and final receipt.
Snapshot serialization restores original knob text, Root name and dirty state;
multiple-context paths fail closed. Native execution may fail for license,
model/format/channel or device reasons even after class discovery succeeds.
The worker restores the source working directory, in-memory script name and
native plug-in search order before execution, so relative media, Root-name
expressions and already loaded Gizmos keep their source evaluation context.
The original script is never saved by the worker. Only paths already trusted
by the running host are reused; startup profiles and temporary files stay isolated.

Use Core job status/cancel; do not invent an ML-specific job id or assume a
timeout rolled back already written checkpoints. Do not enable networked
training, change global device preferences, or silently download model weights.
Inference/Deblur results need a rendered-content check. Upscale must additionally
show doubled dimensions; a node declaration or small preview alone is insufficient.

The native 16.1 knobs differ from parts of the user guide: `halfPrecision`,
`useMultiResolution`, CatFileCreator's non-suffixed scripting names and its
nonempty model ID requirement are
confirmed in a real NukeX 16.1 host. Extra training validation/augmentation knobs,
custom TorchScript user knobs and resume are not exposed by this bounded API.

Primary references:

- [AIR node list](https://learn.foundry.com/nuke/16.1/content/reference_guide/air_nodes/air_nodes.html)
- [CopyCat](https://learn.foundry.com/nuke/16.1/content/reference_guide/air_nodes/copycat.html)
- [BigCat](https://learn.foundry.com/nuke/16.1/content/reference_guide/air_nodes/bigcat.html)
- [Inference](https://learn.foundry.com/nuke/16.1/content/reference_guide/air_nodes/inference.html)
- [Deblur](https://learn.foundry.com/nuke/16.1/content/reference_guide/air_nodes/deblur.html)
- [Upscale](https://learn.foundry.com/nuke/16.1/content/reference_guide/air_nodes/upscale.html)
- [CatFileCreator](https://learn.foundry.com/nuke/16.1/content/reference_guide/air_nodes/catfilecreator.html)
- [TorchScript conversion and NukeX restriction](https://learn.foundry.com/nuke/16.1/content/comp_environment/air_tools/cfc-intro.html)
- [Native command-line training and licensing](https://learn.foundry.com/nuke/16.1/content/comp_environment/air_tools/cc-headless-train.html)
- [Model-authoring contracts](https://learn.foundry.com/nuke/developers/16.1/catfilecreationreferenceguide/index.html)
- [CatFileCreator introduced in 13.1](https://learn.foundry.com/nuke/13.1/content/release_notes/nuke_13.1.html)
