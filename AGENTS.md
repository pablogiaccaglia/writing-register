# AGENTS.md

Every agent working in this repository follows the same guidance, and it lives in [CLAUDE.md](CLAUDE.md). That file explains what the repository is, sets the rules for changing it and lists the commands.

The first rule that matters most is to never edit `vendor/humanizer/`. That directory holds the humanizer skill, copied whole from its upstream repository, and `wr humanize` follows the skill when it rewrites a file. To change what the rewrite does, change the voice in use (`voice/<name>/`) and run `wr voice build`, or change the instructions in `build_prompt`. Never edit a generated `voice/<name>.md`, which `wr voice build` writes.

The second is that only `src/writing_register/spawn.py` may start a model process, because it runs the model inside a sandbox. It sends the prompt on stdin and gives the child process an empty MCP configuration and a neutral working directory. It also passes on only an allowlisted set of environment variables, so the child gets no service credentials and no project instructions. A model call started from anywhere else would skip all of that, so `tests/test_no_second_spawn.py` fails when another file builds a `claude` command.

The third concerns the plugin's hooks in `src/writing_register/hooks.py`. A hook does nothing in a scripted `claude -p` run, never answers with a permission decision, and exits 0 whatever goes wrong. [CLAUDE.md](CLAUDE.md) explains why.
