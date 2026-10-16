# Installation and sign-in

[中文](installation.md) · [README](../README.en.md)

## For users

Give the repository to an agent with local execution. No preinstalled Python, Node, npm, uv, Docker or package manager is needed by the new installer. On macOS, downloading/unzipping the source and double-clicking **安装.command** is also supported; return to your agent afterward for authorization and sign-in guidance.

You approve the official Chrome extension, sign in on platform pages, and complete normal security checks. Use dedicated accounts with little personal information. Never send passwords, cookies or verification codes through chat. WeChat public articles do not require an Official Account administrator login.

## For the installing agent

Read AGENTS.md, README and THIRD_PARTY_NOTICES first. Clone the repository or unpack its release source archive, then run:

```sh
/bin/bash install.sh --clients codex --install-ocr
```

Choose `hermes`, both clients only when requested, or `generic` for configuration export. The installer downloads fixed standalone Python3.11.17 and Node22.23.3 releases, checks committed SHA256 values before execution, and installs OpenCLI1.8.6 plus optional isolated OCR. Runtimes remain under `.state/runtime/`; system runtimes, shell settings and agent dependencies are untouched. Extracted runtimes use about260MB, plus components/OCR. First setup needs network; repeat setup reuses runtimes and preserves existing settings with backups. CODEX_HOME/HERMES_HOME are honored. No detected client produces a generic export, which is not proof that another client was configured.

Requirements are Chrome and basic OS utilities (bash, curl, tar, awk, SHA256). Runtime downloads cover macOS Apple Silicon/Intel and Linux glibc x64/arm64. Automatic dedicated-Chrome launch primarily covers macOS; Linux desktop connection needs agent guidance. Windows and musl are not supported by this installer. Follow host permissions for missing Chrome and normal OS file-opening confirmations; do not disable protections.

Checksum failures stop execution. Interrupted temporary extraction is cleaned while completed components remain. Conflicting runtime versions are not overwritten. A lock prevents concurrent installers; inspect running installation before clearing a stale lock. Do not ask the user to diagnose paths or delete locks blindly. Optional OCR is installed and warmed independently; omitting it preserves other reading. macOS Vision additionally needs installed Swift tools.

If a saved dedicated browser is connected, reuse it. Otherwise explain the official OpenCLI extension's website/debugging/cookie permissions and guide normal authorization:

```sh
/bin/bash run.sh setup --open-browser
```

The macOS default creates a blank normal Chrome profile and records its directory, allowing app-based desktop hosts to select its store window. The agent clicks Add to Chrome; the user approves the final permissions dialog. Permission details come from Chrome and include website data, debugging, downloads and tabs. A host without desktop control must state the remaining manual button. Use `setup --apply --clients codex --wait-for-browser 180 --open-login xiaohongshu` after opening the profile to detect authorization and proceed automatically. Waiting expires into a pending step without reinstalling components.

`--browser-layout isolated` preserves a separate project user-data-dir/process. On macOS its `auto` extension path registers only the official store update URL inside that directory; user enablement is still required. Download was observed, but the enable entry was unclear on this machine, so this is not the novice default. `--extension-install store` skips registration. Never modify the daily profile's global extension registration or policy. Existing legacy isolated profiles retain their original layout. Do not adopt an unconfirmed daily profile, inject cookies, force-load an extension, or modify browser policy. Guide platform sign-in as needed:

```sh
/bin/bash run.sh setup --open-login xiaohongshu
# Also: weibo, zhihu, weixin
/bin/bash run.sh setup --verify --platforms xiaohongshu
```

After the user explicitly reports completing sign-in, the agent can pass `--login-completed xiaohongshu --verify --platforms xiaohongshu`. The marker only resumes the local pending step; actual search and fresh body text are still required. When sign-in is deferred, pending platforms are not visited by setup verification, and other platforms can be used first.

Omitting `--platforms` verifies all four. Acceptance requires actual search and refreshed body text for exactly requested_platforms. setup_progress distinguishes dependencies, dedicated browser, client configuration and actual reading. Never equate identity/connection with readable content.

Finally use the **current agent** to call list_platforms/search/get_task/read_content and inspect actual version/revision. Compatible reading code reloads at idle request boundaries. Launcher/schema/dependency changes and first tool discovery may need host refresh. Do not kill busy MCP processes; use same-version CLI temporarily and report the old registration accurately.

## Daily usage

```sh
/bin/bash run.sh search "个人知识管理工具" --limit 1
/bin/bash run.sh read --content-ref CONTENT_REF
/bin/bash run.sh comments --content-ref CONTENT_REF --limit 2 --replies-limit 1
```

run.sh and generated absolute-path configuration work without system runtimes on the agent PATH. Moving the project requires rerunning setup to update paths. Before updating source, preserve user changes; then rerun installation when appropriate.

Stop on login requirements, security challenges, rate limits, access denial, or unknown command outcomes. Preserve successful content from other platforms. Loading timeouts do not prove an account ban or zero results. Incomplete comments/images remain explicit; use the original content reference for missing-image resume. WeChat comments are unsupported.

## Verification and feedback

```sh
# With Python available to run the test harness, not required by its child installer:
python3 scripts/verify_clean_install.py --portable --output .state/portable-install.json
# Only after authorized sign-in: one bounded real sample per Shanghai day
/bin/bash run.sh stability --run
# Existing local summary only, no platform access
/bin/bash run.sh stability
```

Portable acceptance starts from fresh source with no Python/Node/npm visible on the child PATH, preserves configuration sentinels and calls seven tools using fictional data. It copies no accounts or browser state. Live day receipts remain private in .state/stability; repeats on one date do not add days. The command does not create schedules or upload records. Report platform/version/safe status and redacted reproduction, never credentials, browser profiles, original bodies or signed private URLs.

## Download recovery in a11

Public runtime downloads keep HTTPS and SHA256 verification. Existing environment proxies and static macOS HTTPS proxy settings are reused. Runtime archives preserve partial bytes and resume by HTTP Range, with at most three bounded attempts and a full SHA256 check before execution. Connection failures can fall back to the same official origin directly; HTTP denial and certificate-verification failures do not trigger an automatic route switch. This only affects public dependency downloads, not platform requests or system network settings.

OpenCLI dependencies use the committed npm lockfile and SRI via `npm ci`, with installation scripts and optional native add-ons disabled. The project preserves completed runtimes and a private dependency cache. Setup errors expose a stable `stage`, `reason`, and private `log_file`, rather than a truncated command. An agent can inspect `.state/setup-progress.json` while waiting and rerun the same installer after handling the failed step. The user should not need to reinstall Python or Node manually.

`SOCIAL_READ_DOWNLOAD_ROUTE=proxy|direct|auto` is an optional download-only diagnostic setting for installation agents. Do not paste proxy credentials into feedback. Use [the short feedback card](trial-feedback.md) or the repository issue chooser.
