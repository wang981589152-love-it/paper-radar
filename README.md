# 🌿 植物环境信号 · 论文雷达 (Paper Radar)

为「植物—环境互作」方向定制的每日论文追踪站：自动抓取 **31 本期刊**近三年全部论文（CNS 正刊及子刊智能过滤），按研究主题（环境信号 → 植物激素 → 转录因子 → 生长发育，侧重转录因子调控关系）打分排序，支持**一键中文翻译**与**图形摘要缩略图**。

## 目录结构

```
paper-radar/
├── index.html              # 网页（双击即可打开，无需服务器）
├── data/
│   ├── meta.js             # 全部论文索引 + 统计 + 单位字典 + 图形摘要URL
│   ├── full_2023.js ...    # 按年分片的完整数据（摘要/单位/全部作者），网页按需加载
│   ├── figs_raw.json       # 图形摘要缓存（tier>=2 且有 PMC 全文）
│   └── raw_cache.json      # 原始抓取缓存（增量更新的基础）
└── scripts/
    └── paper_radar.py      # 抓取 + 打分 + 图形 + 建库（唯一脚本，零第三方依赖）
```

## 覆盖期刊（31 本，两类收录策略）

| 类别 | 期刊 | 规则 |
|---|---|---|
| **植物专业期刊**（13本） | PNAS · Plant Cell · Nature Plants · Molecular Plant · New Phytologist · JIPB · Plant Journal · JXB · Plant Physiology · Plant Communications · Cell Research · Annu Rev Plant Biol · Trends Plant Sci | 全量收录 |
| **CNS 及子刊等综合大刊**（18本） | Nature · Science · Cell · Nature Communications · Science Advances · Cell Reports · eLife · Current Biology · Developmental Cell · Cell Host & Microbe · Nature Genetics · Nature Biotechnology · Nature Chemical Biology · Nature Ecology & Evolution · Nature Microbiology · Nature Cell Biology · EMBO Journal · Science Signaling | 查询时按植物/激素关键词预过滤，仅收录主题相关（★及以上）论文 |

数据源：Europe PMC（字段含标题/作者/单位/摘要/DOI/PMID/PMCID，自动剔除更正、信件、社论）。

## 常用命令

```bash
python3 scripts/paper_radar.py full             # 首次全量抓取近三年（有缓存则直接重建）
python3 scripts/paper_radar.py full --refetch   # 忽略缓存，重新全量抓取
python3 scripts/paper_radar.py daily            # 每日增量：抓最近10天新增，滚动保留近三年
python3 scripts/paper_radar.py figs             # 为 tier>=2 且有 PMC 全文的论文抓图形摘要
python3 scripts/paper_radar.py probe            # 查看各期刊论文数量
```

## 加密版站点（AES-256-GCM）

把全部论文数据加密打包，服务器上只有密文，浏览器端用访问密码解锁后运行。适用于「数据不想公开、但想挂在 GitHub Pages 上随时访问」的场景。

```bash
# 安装依赖（仅构建时用，站点本身零依赖）
sudo pip3 install cryptography

# 构建加密版（输出到 docs/，默认密码 plant2026）
python3 scripts/build_encrypted.py --password 你的密码

# 本地预览（加密版必须走 http，file:// 下年度分片会被浏览器拦截）
cd docs && python3 -m http.server 8000
# 浏览器打开 http://localhost:8000 输入密码即可
```

- 加密算法：**PBKDF2(SHA-256, 20 万次) 派生密钥 + AES-256-GCM**，主数据内嵌 HTML，年度分片加密为 `docs/data/full_YYYY.bin`，浏览器用 WebCrypto 原地解密，密码不离开浏览器。
- 支持 `#pw=密码` 的 URL 方式自动解锁（自用方便，注意浏览器历史会记录该 URL）。
- 密码只保存在你手里（构建时传入），仓库里只有密文。

## 每天自动更新（GitHub Pages 部署）

1. 将本目录推送到 GitHub 仓库（`.gitignore` 已排除明文缓存，避免泄露数据）；
2. 仓库 **Settings → Pages → Source** 选 `Deploy from a branch` → 分支 `main` → 目录 `/docs`（这样公开访问的是**加密版**）；
3. 仓库 **Settings → Secrets and variables → Actions → New repository secret** 添加：
   - `PAPER_RADAR_PASSWORD`：你的访问密码（与本地构建一致）
4. 推送 `.github/workflows/update.yml`（本仓库已含），每日 UTC 22:50（北京时间次日 6:50）自动运行：

   - 全量抓取近三年论文（Europe PMC）
   - 抓取图形摘要
   - 用 `PAPER_RADAR_PASSWORD` 重新构建加密版到 `docs/`
   - 自动 commit + push

之后每天打开 Pages 网址即可看到「最新雷达」自动刷新（Europe PMC 数据一般滞后官网 1–3 天，属正常）。

## 相关性打分（在 `scripts/paper_radar.py` 的 `TERMS` 中可自由修改）

| 主题层 | 示例 | 分值 |
|---|---|---|
| 环境信号 | 干旱/盐渍/温度/光/病原免疫/洪涝/营养/重金属 | 每项 +2 |
| 植物激素 | ABA/生长素/乙烯/茉莉酸/水杨酸/赤霉素/BR/细胞分裂素/SL | 每项 +3 |
| 转录因子 | MYB/NAC/WRKY/ERF-AP2/bZIP/bHLH-PIF/TCP/ARF/HD-Zip… | 每项 +2 |
| 调控证据 | ChIP/EMSA/荧光素酶/transactivation/直接靶向 | 每项 +2 |
| 生长发育 | 根/叶/气孔/开花/衰老/产量 | 每项 +1 |

交叉加分：环境×激素 **+3**；激素×转录因子 **+3**；环境×激素×转录因子 **+5**；转录因子×调控证据 **+2**。
分级：≥12 ★★★ 强相关；≥7 ★★ 较相关；≥3 ★ 弱相关；未命中植物门槛 = 主题外收录。

## 迁移到其他方向

改两处即可适配任何领域：
- `JOURNALS`：期刊名称与 ISSN 列表；
- `TERMS`：`(层级, 中文标签, 正则)` 三元组。

## 路线图（可选扩展）

- [ ] 接入 LLM API：批量翻译摘要为中文 + 语义级相关度复筛（替代纯关键词规则）
- [ ] 邮件推送：GitHub Actions 每日把 ★★★ 论文列表发到邮箱
- [ ] 公众号分发：人工一键导出当日精选的公众号排版草稿
- [ ] 添加更多期刊（eLife / Plant Communications / Science Advances / Nat Commun 等）
