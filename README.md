# 抽样回应与多端播放下服务

云课堂观察服务展示设备播放心跳、提问投放和回应原始数；它们不是有效学习分钟或个人认真程度。

## 运行环境

使用 Python 3.11，仅依赖标准库，无外部服务、账号或网络数据。
示例数据使用虚构标识和本地合成记录，不涉及真实学员。

```bash
python3 -m app check
python3 -m app summary
python3 -m app serve --host 127.0.0.1 --port 8080
```

服务提供只读 JSON 查询：`/api/playback`, `/api/probes`。
`/health` 显示存活及表计数，`/api/records?table=表名` 可查看已知数据表。
详细字段语义、计数单位和当前能力见 `docs/数据契约.md`。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q app tests smoke.py
python3 smoke.py
```

测试从实际数据装载进入领域计算、报告与真实 HTTP 服务，并用临时副本验证错误输入。
冒烟程序动态分配本地端口，启动服务后读取全部业务查询并原样输出 JSON，随后正常关闭。
查询不写回数据，因此可以直接重复运行；验证不会访问项目外文件。

## 容器

```bash
docker build -t attention-study:local .
docker run --rm --network none attention-study:local
docker run --rm --network none attention-study:local python3 smoke.py
```

镜像只封装本应用，测试使用回环网络，不依赖宿主端口。
无第三方包依赖，`requirements.lock` 记录此状态。

## 业务办理

sampling_plan.prepare 接受人工设备清单与计划抽样机会；probe_delivery.deliver 执行每人打扰上限、可跳过重复设备；probe_receipts.record 保留真实回应。当前只存计划机会，尚未据实际投放机制修正估计。

业务操作以 JSON 对象 POST 到 `/api/workflow/操作名`，下列操作保留现有函数入参和行为。

- `sampling_plan.prepare`
- `probe_delivery.deliver`
- `probe_receipts.record`

业务贯通用例见 `tests/test_workflows.py`，其中为可直接运行的本地调用。默认状态存于内存；需要重启后保留时，可运行 `python3 -m app serve --state /tmp/learning-service.sqlite3`。状态库只保留本机操作，不向外部发送数据。
