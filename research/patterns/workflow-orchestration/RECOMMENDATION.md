# 建议

MVP 只调度 fetch、normalize、analyze、notify 四类 job；每项有幂等键、租约、重试上限和心跳。服务重启可恢复，H 盘不可用时停止写入并报警。n8n 默认不安装；若后期启用，禁 Code、任意文件、community package、外网监听和交易凭据。
