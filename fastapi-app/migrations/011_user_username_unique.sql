-- 011：为 user.username 补唯一索引（代码审查 🔴#2）
-- admin 表建表时已有 UNIQUE KEY username；user 表缺失，导致并发注册
-- 同名账号可以绕过应用层"先查后建"的检查产生重复行，重复后登录接口
-- 的 get_or_none 会抛 MultipleObjectsFound，账号永久不可用。
--
-- 执行前先确认存量数据没有重复用户名：
--   SELECT username, COUNT(*) c FROM `user` GROUP BY username HAVING c > 1;
-- 如存在重复，需先人工确认保留行（建议保留 id 最小、有登录记录的一行），
-- 清理后再执行本迁移，否则 ALTER 会因唯一冲突失败（安全失败，不损坏数据）。
ALTER TABLE `user`
    ADD UNIQUE KEY `uq_user_username` (`username`);
