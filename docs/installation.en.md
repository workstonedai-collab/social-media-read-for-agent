# Installation, sign-in, and troubleshooting

[中文](installation.md) · [English README](../README.en.md)

## For users

Give the repository URL and the installation prompt in the README to a local agent. The agent checks prerequisites, installs isolated dependencies, merges its own configuration, and performs actual reading. You authorize the official extension, sign in on platform pages, and complete any normal security verification. Use dedicated accounts with little personal information. Do not send passwords, cookies, or verification codes through chat. Reading WeChat public articles does not require an Official Account administrator login.

## For the installing agent

1. Clone the repository and read `AGENTS.md`, the README, and third-party notices. Run `python3 bootstrap.py` to inspect the JSON status. Core: Python 3.9+, Node 20.18.1+, Git, Chrome; OCR: Python 3.10–3.13. Use existing system installers for missing prerequisites, respecting host permissions. Do not alter the agent's own dependency environment.
2. Run `python3 bootstrap.py --apply --clients codex --install-ocr` for Codex, substitute `hermes` for Hermes, or list both only when requested. Omit `--install-ocr` to keep OCR optional. Missing OpenCLI 1.8.6 is installed under `.state/opencli/`; `--bridge project` forces a project-local copy. YAML support and RapidOCR use their own local environments. Model warmup runs at setup.
3. Client settings are backed up before merging. Existing values remain, and an unchanged second run does not rewrite files. Codex respects `CODEX_HOME`; Hermes respects `HERMES_HOME`. For other local clients, `--clients generic` exports configuration; merge the returned file according to the client's rules. Export is not proof that a different client was configured. Cloud-only chat without local execution/stdio cannot directly access the user's browser.
4. Reuse an already saved dedicated connection. If the status requests `dedicated_browser_extension`, explain OpenCLI's website/debugging/cookie permissions and obtain authorization, then run `python3 bootstrap.py --open-browser`. On macOS this opens a project-dedicated Chrome user directory and the official extension store page. Guide normal installation through permitted browser UI; never force-load, modify browser policy, copy a daily profile, or inject cookies. Rerun setup to discover the new connection. If several new connections exist, identify the dedicated window rather than silently choosing one.
5. Run `python3 bootstrap.py --open-login xiaohongshu` (also `weibo`, `zhihu`, `weixin`) as needed. Let the user sign in on the platform page. Never ask them to paste profile IDs, passwords, cookies, or verification codes into chat or configuration.
6. Run `python3 bootstrap.py --verify`. It searches and reads one result from all four platforms. Only all four successful bodies produce `verification.accepted=true`. An identity check alone does not prove search works. Report failures by platform. Stop on verification, rate limiting, or access denial.
7. Call `list_platforms`, `search`, `get_task`, and `read_content` from the **current agent**. Check actual version and loaded/disk revision. CLI acceptance does not prove the client's MCP registration changed. Refresh tool discovery if supported; otherwise use the same-version CLI while accurately describing the old registration. Never kill an active MCP process to force reconnection.

## Updates

Update repository source, rerun setup when dependencies change, and inspect `list_platforms.runtime`. Compatible reading code is reloaded at an idle request boundary without losing tasks or successful OCR checkpoints. The launcher, tool schema, client registration, or dependency environment may require a client refresh. First-time discovery remains controlled by the host. Preserve the dedicated profile and do not repeat extension installation or login unnecessarily.

## Failure handling

| Status or symptom | Agent action |
| --- | --- |
| `adapter_missing` | Resolve the project-local OpenCLI installation and rerun setup |
| `bridge_unavailable` | Check the saved dedicated browser, extension, and local bridge; do not adopt an unconfirmed daily profile |
| `login_required` | Open the relevant platform in the dedicated profile for normal user sign-in |
| `verification_required`, `rate_limited`, access denied | Stop; allow normal user/platform resolution; no account/proxy rotation |
| `navigation_loading_timeout` | New page did not commit within its budget; the owned test tab is closed; do not interpret as an account ban |
| `content_loading_timeout`, `timeout` | Preserve successful platform results and report the failed stage; no unlimited retries or claims of zero search results |
| `source_context_changed` | Search again to obtain a fresh result context; never silently replace an article with a similar title |
| Partial OCR or text | Report missing positions/errors and use the same content reference to resume when appropriate |
| Old version in current client | Compare runtime revisions; refresh if supported, otherwise explicitly use CLI without claiming the old registration updated |

Automatic browser creation is accepted primarily on macOS with Chrome. Linux covers core behavior and account-free installation/protocol checks. Windows browser setup, new-machine signed-in onboarding, and multi-day live stability remain unverified. See the [validation history](validation.md).


## 按需接通 · Connect only what you need

用户只需要一个平台时，由 Agent 先处理该平台的登录与验收：

```sh
python3 bootstrap.py --verify --platforms xiaohongshu
python3 bootstrap.py --verify --platforms weixin
```

省略 `--platforms` 时仍验收四个平台。`accepted=true` 只代表 `requested_platforms` 中的平台本批搜索及刷新正文通过；不把一个平台通过写成四平台可用。`setup_progress` 分别报告依赖、专用浏览器、客户端配置、真实读取四个阶段。用户不需要看内部资料编号，由 Agent 将待完成动作说清楚。验收会刷新正文，普通重复阅读仍可使用缓存。

For a user who only needs one platform, the agent can verify that platform first using the commands above. Omitting `--platforms` checks all four. `accepted=true` applies only to `requested_platforms`. `setup_progress` separates dependencies, dedicated browser, client configuration, and actual reading. Verification refreshes article bodies; ordinary repeat reads may still use cache. The agent should explain pending actions without asking the user to handle internal profile IDs.
