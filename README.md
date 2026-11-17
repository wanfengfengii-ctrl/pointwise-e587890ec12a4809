# Research Capture Archive

Python 3.11 标准库离线资料留存工程。采集员登记研究请求，从调用方提供的离线 HTTP 来源取得资料；合作方也可交付完整 WARC response 包。复核员读取固定成果、核对原始证据并导出，研究人员可在独立目录重新导入。

## 运行

在项目根目录运行：

```sh
python3.11 -B run_checks.py
python3.11 -B run_checks.py --demo
python3.11 -B revisit_checks.py --verify
python3.11 -B revisit_checks.py --demo
```

第一条运行全部 unittest 回归（含 tests/ 原回归与 revisit_checks.py 的联合业务核验），失败返回非零。第二条读取 samples/daily.json 与 samples/partner.warc，实际执行登记、采集、导入、重开、复核、导出和独立再导入。第三条独立核验 revisit 联合夹具，第四条展示 revisit 批次重开后的真实状态、文本、继承摘要与来源链。四者只使用各自创建的临时数据目录。运行入口同时登记在 .pointwise-runtime.json 和 run_manifest.json，无安装步骤。

partner.warc 的原始 HTTP 块为 85 字节，SHA-256 为
74fa5ea579a05bfbda983b7d9c004330a29437c33c45bcde1bc0e0fff0ed9ab4。
正文为六字节 hello 加换行，SHA-256 为
5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03。
daily.json 的固定时间为 2026-01-02T03:04:05Z；两条资料分别得到状态 203 的“研究资料\n甲乙\n”和状态 204 的空文本。

## 日常流程和状态

1. submit 接纳稳定任务 ID 及完整请求，建立 pending；相同请求重投不重置状态。
2. run 原子领取 pending 或 retryable，增加 attempts 并变为 running。
3. source(request) 返回 bytes 或单遍 bytes 迭代器；中断进入 retryable，默认第三次失败后 failed。
4. 完整响应经过状态与有序头解析、传输解码、内容解码和严格 UTF-8 解释；非法内容使任务 failed。
5. 完整 response 及本记录解释进入私有暂存区；外部 WARC 包也经过同一解释和暂存门槛。
6. 发布全部对象与有序清单，目录事务提交后才可读取；采集回执与单记录批次一同提交，随后任务变为 succeeded。
7. 复核重新读取原始对象，校验摘要并重新解释，确认文本一致后返回固定成果。
8. 导出完整 response，再导入独立归档；保持记录身份、顺序、原始 HTTP 字节、状态及文本。

每个采集任务拥有稳定的单记录批次；一项失败不撤销其他成功任务。任务库与归档库分开。重复初始化保留用户已加入的合法任务和批次；不兼容版本报错。目录对象不可覆盖或删除，发布其他批次不会改变旧批次。

## 使用公开 API

```python
from archive.database import ArchiveDatabase
from archive.bodies import BodyStore
from archive.publication import Publisher
from capture.store import JobStore
from capture.jobs import CaptureRequest, JobService
from capture.schedule import CaptureRunner
from warc.importer import ResponseImporter
from review.verify import VerificationService
from review.replay import ReplayService
from review.export import ExportService

database = ArchiveDatabase("data/archive")
bodies = BodyStore("data/archive")
publisher = Publisher(database, bodies)
jobs = JobStore("data/tasks.sqlite3")
service = JobService(jobs)
runner = CaptureRunner(jobs, publisher)
verifier = VerificationService(publisher.catalog, bodies)
replay = ReplayService(verifier)
exporter = ExportService(publisher.catalog, verifier)
importer = ResponseImporter(publisher)

service.submit([CaptureRequest("paper-1", "https://example.test/paper")])
def source(request):
    return b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"
job = runner.run(["paper-1"], source)[0]
result = replay.replay(job.batch_id, job.record_id)
assert result.text == "hello"
verifier.verify_record(job.batch_id, job.record_id)
with open("data/export.warc", "wb") as sink:
    exporter.export_batch(job.batch_id, sink)
```

构造时状态所有者自动初始化，不维持长期 SQLite 连接。对象重建即可重开。单工作者恢复时先调用 runner.reconcile()：遗留 running 且有匹配固定回执的任务补记 succeeded，来源不再调用；无回执则按剩余预算进入 retryable 或 failed。正在运行时不要将 reconcile 当成并发领取入口。默认时钟为当前 UTC 秒；JobStore 的 clock 参数可注入固定 UTC 时间。

CaptureRequest 字段为 job_id、target、method、headers、body。方法转大写，头名转小写并去值两端水平空白；头顺序、重复字段和值保持，正文逐字节参与指纹。同 ID 改正文、头值或其他请求内容整次 submit 拒绝。JobService.submit 返回 CaptureJob 列表，run 返回各任务当前 CaptureJob；running、succeeded、failed 不调用 source。未知任务抛 KeyError。

ResponseImporter.import_responses(chunks,batch_id) 返回 BatchSnapshot，含有序 record_ids。chunks 为有限单遍 bytes 迭代器，允许任意分片及空分片。格式、边界、身份或解释错误抛 ValueError，整批不可见。相同 batch_id 和相同完整有序清单幂等；异内容抛 BatchConflict。清单覆盖 WARC 元数据、原始 HTTP 及各解释层级证据，分片方式不参与身份。

ReplayService.replay(batch_id,record_id) 返回 ReplayResult：状态、有序头、内容正文 bytes、文本及原始/传输/内容层摘要。VerificationService.verify_record 返回 VerifiedRecord，包含原始长度、摘要及重新解释结果。未知批次和记录分别抛 BatchNotFound、RecordNotFound；原始对象缺失或损坏、缓存文本不一致抛 RecordVerificationError。

ExportService.export_batch(batch_id,sink) 接二进制 write 端口并返回 ExportResult。正短写会继续，零进展抛 WarcWriteError，输出异常原样传播。失败 sink 中可能已有前缀，调用方必须丢弃；归档不变。复核与导出都实际消费重新校验的原始证据。

## 有限格式和计算口径

WARC 仅支持 WARC/1.1 自包含 response。六个必需且唯一字段为 WARC-Type、WARC-Record-ID、WARC-Target-URI、WARC-Date、Content-Type 和 Content-Length；可选的唯一 WARC-IP-Address 为合法 IP 地址。字段是可打印 ASCII，日期是 UTC 秒，目标是 HTTP(S) URI，Record-ID 是尖括号包围的绝对 URI。其他 WARC 字段、记录类型和记录间引用不在本版范围内。每块长度按 HTTP 原始字节计算，记录后必须有 CRLFCRLF；空包、重复 ID、残缺尾部均拒绝。导出保持支持的元数据和顺序，重新计算字节长度。

HTTP 仅接 HTTP/1.0 或 HTTP/1.1 的 200–599 最终状态、无折行的头字段，以及明确 Content-Length 或 Transfer-Encoding: chunked。相同的重复长度允许，冲突长度、同时声明两种边界、未知传输编码、缺边界均拒绝。chunked 仅接受十六进制块长，不支持块扩展；支持普通 trailer，但 trailer 不能声明边界或内容解释字段。零块及 trailer 必须完整结束，额外字节拒绝。

先去除传输编码，再按本响应 Content-Encoding 处理 identity 或 gzip，最后按缺省或声明的 UTF-8 严格解码。重复内容类型/编码字段、其他字符集、损坏 gzip 或非法 UTF-8 均拒绝。文本忠实保留 Unicode、空白和换行，空正文得到空串。

SHA-256 分别覆盖原始 HTTP、传输解码字节、内容解码字节和 UTF-8 文本；长度均以字节计。原始对象去重只节省存储，不合并 Record-ID。暂存和持久记录绑定本条完整响应，没有通用业务元数据槽。处理可全量物化，无吞吐、并发或峰值内存保证；没有网络抓取实现。

## Revisit 正文层级与来源归属

ResponseImporter.import_capture(chunks,batch_id) 在同批自包含 response 之外接纳同批 revisit 记录，返回原 BatchSnapshot。revisit 以 WARC-Refers-To 明确引用同批记录 ID，可前向、多跳，完整链末端必须是实体 response；相同正文摘要不能替代指定 Record-ID。revisit 的 HTTP 块只含状态行、有序头和结束头区的 CRLFCRLF，边界仅支持 Content-Length 且必须等于继承正文字节数。

正文层级固定为：原始 HTTP 块（raw，本记录实际字节）→ 去除传输编码（transfer）→ 去除内容编码（body）→ UTF-8 文本（text）。revisit 记录的 raw、状态与有序头属于本记录；transfer 层正文继承来源链末端实体 response 的去传输编码字节，不沿用任何目标记录的状态、头或最终文本；body 与 text 按本记录自己的 Content-Encoding 与字符集解释。归档固定每条记录的来源关系，重开后不依赖输入包。ReplayResult 与 VerifiedRecord 的 source_chain 按从当前记录到实体 response 的次序给出记录 ID，response 为自身单元素链；raw_digest 始终是本记录原始 HTTP 块摘要，transfer_digest 是继承正文摘要，body_digest 是本记录内容解码正文摘要。每次复核与回放都重新读取链上各条原始证据，核对关系、正文摘要与缓存文本。

重复 ID、缺目标、引用环、坏 profile、摘要错误、非法 HTTP、继承长度不符或解释失败均整批拒绝，旧成果不变；相同 batch_id 与相同有序原记录及关系重投幂等，改变顺序、WARC 元数据、原 HTTP 块或明确引用目标抛 BatchConflict。revisit 批次不开放导出；原 import_responses、采集回执恢复、复核与 response 批次导出行为不变。

## 回归覆盖

tests/test_capture.py 覆盖两任务采集、身份冲突、领取互斥、空正文、预算、关闭重开及发布与任务落库之间的恢复。
tests/test_import.py 覆盖逐字节/合并/空分片双记录、中文长度、chunked 加 trailer 承载 gzip、身份和坏编码拒绝、坏末记录整批保全。
tests/test_archive.py 覆盖有序完整身份、重投冲突、目录提交失败、共享对象保全、重复初始化和版本拒绝。
tests/test_review.py 覆盖固定读取、中文/空正文/同摘要异身份的独立再导入、短写和输出失败，以及原始对象被改动、截断、删除和缓存文本损坏的拒绝。
tests/test_revisit.py 覆盖 response 摘要核对、单跳与多跳前向链、revisit 边界与头规则、同字节异身份、身份冲突、中间证据损坏和入口隔离。
revisit_checks.py 以独立字节夹具核验前向链导入、整批拒绝保全、发布故障和链上原证据重验，并演示重开后的真实回放。

Publisher 可注入 fault(event)，事件为 before_commit 与 after_commit；CaptureRunner 的 fault 在 after_publish 调用。端口异常用于受控验证中断边界；发布失败不伪装成功，已提交成果由固定回执恢复。
