# 数据迁移 / Data migration

首版SQL位于src/studyos/schema.sql，由schema_migrations和user_version登记。不在查询时隐式升级。未来模式升级需明确迁移脚本、备份、校验及恢复说明。

The initial SQL schema is src/studyos/schema.sql, registered via schema_migrations and user_version. Queries never implicitly upgrade it. Future schema changes require explicit migration scripts, backups, verification, and restore instructions.

migrate命令仅支持示范旧结构study_data/tracks/legacy_exam/config.json及通用偏好。它不会导入未知学习成绩或切换活动根。其他旧结构应先定义证据映射，再开发兼容器，不修改原系统。

The migrate command supports only the example legacy structure study_data/tracks/legacy_exam/config.json and general preferences. It does not import unknown grades or switch the active archive. Define evidence mappings and adapters for other structures while leaving the source system unchanged.
