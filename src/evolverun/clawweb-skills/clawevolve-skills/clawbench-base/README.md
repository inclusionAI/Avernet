# ClawBench Base

`clawbench-base` provides the shared local runner and result-processing code used by the ClawBench Skills in this directory.

## Open-source origin and license

ClawBench Base is adapted from [PinchBench](https://github.com/pinchbench/skill).

Copyright (c) 2026 PinchBench

The portions derived from PinchBench are distributed under the MIT License. See [LICENSE](LICENSE) for the complete license text. Additions and modifications made by this project are maintained by Avernet and ClawBench contributors.

## Usage

Runtime values such as the AgentEvolve URL, model endpoint, model name, API key and output directory must be supplied by the caller. Do not store credentials in this directory.

For the AgentEvolve Singlebox flow, start Avernet first and then start AgentEvolve. AgentEvolve passes the selected model, local Bot workspace and callback URL to the Skill runner.

Run focused tests from `clawbench-base` so its local Python modules are available:

```bash
python3 -m pytest -q tests
```
