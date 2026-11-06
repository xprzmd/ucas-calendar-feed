# 果壳课表日历订阅源

把学校课表生成 `.ics`，通过 GitHub Actions 定时刷新、GitHub Pages 托管，用 Apple 日历以「订阅日历」的方式添加。学校课表一变，下次刷新自动更新。

不是学校官方服务。接口协议来自 `ucas_signin_ios-share` 项目的 `GuokeCore`（AGPL-3.0-or-later）。

## 组成

- `fetch_ics.py`：用学校账号登录轻新课堂接口，拉取未来 `FEED_DAYS` 天（默认 180）的课表，生成 iCalendar。
- `.github/workflows/update.yml`：每 6 小时跑一次，把生成的 `calendar.ics` 推到 `gh-pages` 分支；Pages 从该分支发布。

## 部署步骤

1. 在 GitHub 新建一个 **public** 仓库（免费账号的 Pages 只支持公开仓库；订阅源 URL 也因此是公开可访问的）。
2. 把本目录内容推到仓库根目录。
3. 仓库 **Settings → Secrets and variables → Actions**，添加两个 secret：
   - `SCHOOL_ACCOUNT`：App 登录用的账号（SEP 邮箱 / 学号，与 App 里填的一致）
   - `SCHOOL_PASSWORD`：密码
4. 仓库 **Settings → Pages**，Source 选 **Deploy from a branch**，分支 `gh-pages`、目录 `/ (root)`。如果还没有 `gh-pages` 分支，先做第 5 步再回来设置。
5. **Actions** 页签启用 workflow，手动点一次 **Run workflow**，确认运行成功且生成 `calendar.ics`。
6. 订阅地址：`https://<你的用户名>.github.io/<仓库名>/calendar.ics`

## 在 Apple 日历里订阅

- iPhone：设置 → 日历 → 账户 → 添加账户 → 其他 → **添加已订阅的日历**，粘贴订阅地址。
- Mac：日历 → 文件 → **新建日历订阅**，粘贴地址。
- 刷新频率由系统决定：iPhone 在 设置 → 日历 → 账户 → **获取新数据** 里选「每 15 分钟」；Mac 在 日历 → 设置 → 通用 → **刷新日历** 里设置。iOS 最低 15 分钟，不会实时。
- 订阅日历是只读的；学校课表变更、取消的课会在下次刷新后反映到日历。

## 可调参数

在 workflow 的 `Generate feed` 步骤环境变量里：

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `FEED_DAYS` | `180` | 从今天起生成多少天的课表 |
| `ALARM_MINUTES` | `10` | 每节课提前多少分钟提醒；`0` 关闭 |
| `CAL_NAME` | `果壳课表` | 日历名称 |

## 本地测试

无需学校账号的示例输出：

```sh
python3 fetch_ics.py --demo --out /tmp/demo.ics
```

用真实账号在本地验证（密码只经过环境变量，不落盘）：

```sh
SCHOOL_ACCOUNT='你的账号' SCHOOL_PASSWORD='你的密码' python3 fetch_ics.py --out /tmp/my.ics
open /tmp/my.ics
```

确认无误后再交给 GitHub Actions。

## 注意

- 密码只存在仓库 Secrets；但生成的 `calendar.ics` 在公开仓库的 Pages 上是**公开可访问**的，里面有课程名、教师、时间。介意的话把仓库名或订阅路径做得不易猜到（Pages 路径固定为仓库名，可改写 workflow 输出到带随机串的子目录）。
- 学校密码修改后，更新 secret，否则当次运行会失败，订阅会停留在上一次的内容。
- 如果某次运行一节课都没取到（登录失效或接口变更），脚本会以失败退出、不覆盖已发布的订阅，避免日历被清空。查看 Actions 日志即可发现。
- GitHub 定时任务可能延迟几分钟到几十分钟；免费账号的定时 workflow 在仓库 60 天无提交后会被自动停用，本 workflow 每次运行都会提交，正常刷新时不会触发该限制。
- GitHub 托管 runner 在海外；如果学校接口对海外 IP 有拦截，运行日志会显示连接失败。此时改用 self-hosted runner（放在国内机器上），workflow 只需改 `runs-on`。
- 只做只读同步：不提交签到、不写回学校系统。签到仍需用 App。
