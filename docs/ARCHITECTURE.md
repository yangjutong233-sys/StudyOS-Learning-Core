# 设计与边界 / Architecture and boundaries

```text
Learner + AI assistant
        | validated CLI requests
        v
Python CLI -> SQLite + immutable evidence archives
        |              |
        v              v
Generated handoffs     Backup + restore verification
        |
        v
Teaching conversation (learner shares selected excerpts)
```

SQLite保存当前知识状态、会话、原答、判定、报告索引、复习及审计。原报告保留在本地records，材料通过登记的路径、版本和哈希定位。配置由CLI受控变更并登记哈希；交接和仪表盘是派生视图。

SQLite stores knowledge state, sessions, answers, evaluations, report indexes, reviews, and audits. Original reports remain in local records; registered paths, versions, and hashes locate materials. CLI-managed configuration changes register hashes. Handoffs and dashboards are derived views.

## 代码目录 / Source layout

| 目录 / Path | 内容 / Purpose |
| --- | --- |
| src/studyos | CLI、业务规则、SQLite模式、事务、审计和备份 / CLI, business rules, schema, transactions, audits, backups |
| tools/setup.py | 新建空档案，自主学习配置 / Empty archive setup with learner-directed defaults |
| tools/dashboard.py | 本地仪表盘与时长追加更正 / Local dashboard and append-only duration corrections |
| tools/acceptance_walkthrough.py | 临时根内公开CLI合成验收 / Synthetic CLI acceptance in temporary roots |
| tools/check_public_tree.py | Git待发布文件检查 / Git publication file checks |
| tests | 独立临时根、合成资料和回归验证 / Isolated synthetic fixtures and regression checks |
| schemas / examples | 接口模式和格式样例 / Schemas and format examples |
| skills/studyos | 可直接阅读的运营手册 / Readable AI archive operations skill |

没有模型调用、云端学习数据库、后台定时守护或隐式初始化。仪表盘端口仅绑定127.0.0.1。当前是可信单用户本地工具，不是多租户服务。

There are no model API calls, cloud learner databases, background schedulers, or implicit initialization. The dashboard binds to 127.0.0.1 only. This is a trusted, single-user local tool, not a multi-tenant service.

## 规则与兼容 / Policies and compatibility

公开版默认时间草案为空；工具快速初始化启用USER_ONLY、PROGRESS_ONLY及任意格式真实报告接收。底层CLI还保留旧的非自主模式供兼容测试和显式配置使用，不把它当默认操作流程。

Public schedule defaults are empty drafts. Quick setup enables USER_ONLY, PROGRESS_ONLY, and authentic reports in any format. The low-level CLI retains a legacy non-self-directed mode for compatibility testing and explicit configuration, not as the recommended workflow.

旧迁移器仅识别示范结构study_data/tracks/legacy_exam及可选通用偏好；不自动映射任意旧系统。若含学习证据，必须先定义映射，否则拒绝；正式迁移与数据源切换分别授权。

The legacy migrator recognizes only the documented study_data/tracks/legacy_exam structure and optional general preferences. It does not support arbitrary old systems. Learning evidence requires an explicit mapping, otherwise migration is rejected. Migration and switching the active data source require separate authorization.
