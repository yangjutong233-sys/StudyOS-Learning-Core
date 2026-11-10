# 隐私与发布 / Privacy and publishing

公开源码与个人档案分离。建议把运行根放在.local/studyos（默认被忽略），也可以使用仓库外的明确路径。数据库、报告、课件、配置、交接、备份和快照属于本地用户数据。

Public source and personal archives are separate. Use .local/studyos (ignored by default) or an explicit path outside the repository. Databases, reports, courseware, configuration, handoffs, backups, and snapshots are local user data.

## 提交前 / Before committing

```sh
git status --short
git diff --cached --stat
python tools/check_public_tree.py
```

检查器只读取Git索引中的待发布内容，检查运行目录、二进制文件、个人路径和常见凭据格式。任何检查器都不能替代人工语义审查；不要使用git add -f加入被忽略的档案，也不要把真实学习材料换名放入源码目录。

The checker reads publication candidates from the Git index and checks runtime directories, binary files, machine-specific paths, and common credential patterns. No checker replaces human semantic review. Do not force-add ignored archives or rename real learning content into source directories.

如果旧仓库历史包含个人数据，新公开仓库必须使用独立的干净历史；删除当前文件不会删除旧提交中的内容。个人数据备份使用程序的SQLite Backup API，不依赖Git。

If an old repository history contains personal data, publish with independent clean history. Removing current files does not remove past commits. Back up learner data using the SQLite Backup API, not Git.

## AI 与网络 / AI and network boundaries

程序不主动把学习数据发给模型服务。你在外部AI聊天中选择上传的文件会受该服务的数据规则约束。Git推送只发布经你核对的源码，任何档案导出或分享都应由用户明确选择。

The program does not send learning data to model services. Files you choose to upload to an external AI chat are subject to that service's data policies. Git pushes publish reviewed source; archive exports or sharing require the learner's explicit choice.
