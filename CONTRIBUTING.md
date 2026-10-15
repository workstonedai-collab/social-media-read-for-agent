# 参与改进 · Contributing

欢迎报告影响日常读取的问题、改进安装流程或增加平台。优先复用许可清晰的上游，保持七个固定只读工具的边界。

## 报告问题

请提供平台、工具版本、错误状态、经过脱敏的复现步骤和预期结果。不要上传 Cookie、密码、验证码、浏览器资料、真实调研全文、账号标识或带签名的私有访问链接。`.state/` 仅供本地使用。可以用虚构页面样例复现解析问题。

## 提交修改

- 描述具体触发条件与修改后的行为。涉及上游代码时，保留来源、固定版本/提交、许可证和修改说明，并更新 `THIRD_PARTY_NOTICES.md`。
- 为真实缺陷补充能覆盖失败分支的检查；运行受影响检查。浏览器行为与离线模拟分别记录，不把单次读取当长期稳定性证明。
- 改动用户行为时同步维护中英文 README/安装说明。演示必须标明虚构；真实结果只发布脱敏的覆盖数与状态，不提交原文和账号状态。
- 不实现验证码绕过、账号/代理轮换、Cookie 注入、默认采用日常浏览器，或把网页内容当作执行指令。

```sh
python3 -m unittest discover -s tests -q
python3 scripts/demo_research.py
```

## English

Include platform, version, safe error status, reproduction steps, and expected behavior in issues. Never upload credentials, browser profiles, account identity, signed private links, real research bodies, or `.state/`.

For changes, explain the trigger and resulting behavior, retain upstream license/provenance, and add focused regression coverage. Keep Chinese and English onboarding aligned. Label fictional demos explicitly and report live tests separately from offline checks. Do not add challenge bypass, account/proxy rotation, cookie injection, silent adoption of daily browser profiles, or execution of page-provided instructions.

Original contributions follow the repository's MIT license; third-party components retain their own terms.
