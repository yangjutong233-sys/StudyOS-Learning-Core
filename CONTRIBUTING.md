# 贡献 / Contributing

欢迎修复、文档改进和可复现的功能建议。提交issue或PR前请移除真实报告、教材和个人路径；用临时根和合成数据展示问题。

Bug fixes, documentation improvements, and reproducible feature proposals are welcome. Remove real reports, textbooks, and personal paths from issues and pull requests. Reproduce problems using temporary roots and synthetic data.

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
python tools/check_public_tree.py
```

修改证据、复习、计划或备份规则时验证真实业务不变量；不通过降低标准、伪造原答或删除失败测试让检查通过。新增初始化不能产生虚构学习记录。不要在自己的正式学习根运行验收脚本。

For evidence, review, planning, or backup changes, verify business invariants. Do not weaken standards, fabricate answers, or delete failing tests to pass checks. Initialization must not create fictional study. Never run acceptance scripts against a real learner archive.

提交前明确暂存源码文件，人工审查并执行隐私检查器。MIT许可适用于你的贡献；第三方教材或未经授权的媒体不属于可提交内容。

Stage source files explicitly, inspect them, and run the privacy checker. Contributions are under the MIT license. Do not submit third-party textbooks or unauthorized media.
