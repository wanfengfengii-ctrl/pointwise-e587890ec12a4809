# Revisit 采集成果

## 已有链路与本次入口

README.md 中的 V1 业务、全部原接口及 tests/ 下原文件保留。V1 仅实现自包含 response。本次在同一个 warc.importer.ResponseImporter 上新增 import_capture(chunks, batch_id)，返回原 BatchSnapshot，record_ids 保持输入记录顺序。chunks 为有限单遍 bytes 迭代器，任意切分及空块均合法。允许全量处理、复制正文及落盘，不增加内存、扫描或吞吐指标。

新入口接纳同批 response 和下述 revisit。原 import_responses 继续只接纳原 V1 response，原有成果、回执恢复、复核和 response 批次导出行为保留。本次不扩展新 revisit 批次的导出，不增加网络、跨批引用、并发或恢复协议。

## 字节格式

继续使用 WARC/1.1、CRLF、按字节计的外层 Content-Length 和记录后 CRLFCRLF。原六个必需唯一 WARC 字段及可选 WARC-IP-Address 的规则不变。字段名大小写不敏感，字段顺序保留；类型、profile 和 Record-ID 值区分大小写。新入口仅增加下列字段：
- response 可选唯一 WARC-Payload-Digest；不允许 WARC-Refers-To 或 WARC-Profile。
- revisit 必须另有各一个 WARC-Refers-To、WARC-Profile、WARC-Payload-Digest。WARC-Type 为 revisit，Content-Type 仍为 application/http; msgtype=response。
- WARC-Profile 必须为 http://netpreserve.org/warc/1.1/revisit/identical-payload-digest。
- WARC-Refers-To 是与 Record-ID 同格式的同批明确记录 ID，不按 URL、日期或摘要寻找替代目标。
- WARC-Payload-Digest 格式限定为 sha256: 后接 64 位小写十六进制，覆盖去除 HTTP 传输编码后、解除内容编码前的正文。response 有此字段时也须核对。其他 WARC 字段和类型仍拒绝。

response 的 HTTP 块沿用全部 V1 规则。revisit 的 HTTP 块仅含状态行、有序头及结束头区的 CRLFCRLF，后面没有正文。其状态和头按 V1 语法校验，但边界仅支持 Content-Length，不允许 Transfer-Encoding；重复相等长度可接受。该 HTTP Content-Length 必须等于继承正文的字节数，而外层 WARC Content-Length 仅计本条实际头块字节数，两者不能混用。仍要求 200–599 最终状态、唯一内容类型及内容编码；内容编码为缺省 identity、identity 或 gzip，字符集为缺省或声明的 UTF-8。

## 共同结果与失败边界

每条记录在批内身份唯一。revisit 可前向、多跳引用，但完整链最终必须到 response；每条 revisit 继承最终实体 response 去除传输编码后的同一正文原字节，不沿用任何目标记录的 HTTP 状态、头或最终文本。每条记录均须按自己的 Content-Encoding 和字符集成功解释。相同正文摘要不能代替指定 Record-ID，来源链按从当前记录到实体 response 的次序固定。

空包、重复 ID、缺目标、引用环、目标类型不符、坏 profile、摘要错误、非法 HTTP、继承长度不符、gzip 或 UTF-8 解释失败及截断都抛 ValueError，整批不发布；来源迭代异常向上传播且不发布。所有记录与来源证据完整后才一次发布，发布前失败保全旧批次，私有候选清理沿用原规则。Publisher 的 before_commit 故障端口同样适用新入口，提交失败不留下可读前缀。

相同 batch_id、相同有序原记录及关系重投幂等；仅改变分块方式不改变成果。改变顺序、WARC 元数据、原 HTTP 块或明确引用目标即为异成果，即使最终文本及正文摘要相同，也抛 BatchConflict，原成果不变。

## 固定归档、回放与核账

导入、发布及复核需要共同支持当前头与继承正文分属不同记录的成果。持久化组织由实现选择；须保存本批原始证据及固定关系，重开后不能依赖输入包或来源迭代器。原数据库和既有成果须仍可读取，不要求重写旧批次。

在原 ReplayResult 上增加 source_chain，类型为记录 ID 的 tuple；新入口导入的 response 为自身单元素链，revisit 为完整有序链。原 status、headers、body、text 字段分别返回当前记录的状态、有序头、内容解码正文和文本。transfer_digest 为继承正文 SHA-256 小写十六进制，body_digest 为本记录内容解码正文摘要，raw_digest 始终为当前记录实际原始 HTTP 块摘要，不是合成响应摘要。

VerificationService.verify_record 同样支持新成果，并在原 VerifiedRecord 上增加 source_chain。record.raw 和 record.headers 保留当前原记录，raw_length/raw_digest 对应其实际 HTTP 块；consistent 仅在重新核验成功时为 True。每次复核和回放实际读取链上各条原始证据，核对指定关系、正文摘要及当前解释与缓存文本，不能仅信任预存来源链或最终文本。依赖的原始对象损坏或缺失仍抛 RecordVerificationError，不能以物化副本绕过；未知批次、记录沿用原异常。已有 Catalog 记录的 ref 仍标识该条原 HTTP 块。

示例由 revisit_checks.py 的 joint_fixture() 独立造包：输入顺序 S、R、A、B；S 引用 R，R 明确引用 B；A 和 B 的 HTTP 字节相同，以 chunked 承载 gzip 字节。R/S 仅含自身 Content-Length 和 gzip 头，分别为 203/202，并带各自 X-Capture。重开后 R 为 203、文本“研究资料\n甲乙\n”、链 R→B；S 为 202、同文本、链 S→R→B。A 不得代替 B。fixture 中 Record-ID 为 <urn:revisit:名字>。

## 交付核验和演示

补充简短的正文层级与来源归属说明，保留全部原测试字节。revisit_checks.py 是独立字节夹具和业务核验，不含目标实现；--selfcheck 仅自检材料，不能代替新功能通过。完成后运行：
- python3.11 -B run_checks.py
- python3.11 -B revisit_checks.py --verify
- python3.11 -B revisit_checks.py --demo

verify 中任一业务断言失败须非零退出。可添加对应本契约的测试，不把给定夹具当成全部合法输入。沿现有 .pointwise-runtime.json 的格式更新 verify，使原回归和新成果核验都实际执行，demo 展示真实导入、对象重开后的状态、文本、继承摘要及来源链。清单保留 version=1、kind=cli、setup 的 argv 列表和 verify/demo 的单个 argv 数组；可使用包装入口合并核验。原 run_checks.py --demo 仍可使用。
