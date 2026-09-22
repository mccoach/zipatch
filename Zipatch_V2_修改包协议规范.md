# Zipatch V2 修改包协议规范

当前版本：V2.8

本文档规定 AI 构建 Zipatch V2 修改包时必须遵守的格式、OP 选择、路径规则、内容定位规则和输出自检规则。

协议名固定为：

```text
AI_FILE_PATCH_V2
```

AI 输出修改包时，整个修改包必须放在一个 Markdown `text` 代码块中，确保用户可一键复制完整原文。外层代码块围栏长度必须大于修改包正文中出现的任何连续反引号长度；默认至少四反引号，若正文含四反引号则继续加长，禁止内容溢出。

---

## 1. 基本结构

```text
<<AI_FILE_PATCH_V2 boundary="AI_PATCH_BOUNDARY_唯一边界字符串">>
---OP 操作类型 id="唯一OP标识" path="相对路径" 参数="..."
---CONTENT
正文内容
AI_PATCH_BOUNDARY_唯一边界字符串
---END_OP
<</AI_FILE_PATCH_V2>>
```

要求：

1. 首个非空行必须是包头独占一行，最后非空行必须是包尾独占一行，包头、包尾行内以及包头行前、包尾行后不得出现不属于包头、包尾的其他非空内容；
2. 一个修改包内允许一个或多个 OP，多个 OP 按书写顺序排列；
3. 每个 OP 以 `---OP ...` 开始，以 `---END_OP` 结束；
4. 每个 OP 必须在 `---OP` 行声明唯一 `id` 字段；
5. `---CONTENT`、`---OLD`、`---NEW` 后的正文必须由 `boundary` 单独成行结束；
6. 正文直接写入文本块，不使用字符串转义。外层 Markdown 闭合围栏不属于修改包载荷，必须在包头行前和包尾行后另起一行书写，闭合围栏符号不得混入包头、包尾行内。

包头：`<<AI_FILE_PATCH_V2 boundary="AI_PATCH_BOUNDARY_...">>`
包尾：`<</AI_FILE_PATCH_V2>>`

---

## 2. boundary

`boundary` 是正文块结束标记。

要求：

1. 必须以 `AI_PATCH_BOUNDARY_` 开头；
2. 至少 32 个字符，建议不超过 160 个字符；
3. 只能包含字母、数字、下划线、短横线和点；
4. 不得等于协议关键字；
5. 不得在正文中作为单独一行出现；
6. 同一修改包内所有正文块共用同一个 `boundary`；
7. 必须包含 14 位创建时间码 `YYYYMMDDHHMMSS`。

示例：`AI_PATCH_BOUNDARY_TASK_20260713004354_A1B2C3D4E5F6`

---

## 3. 路径规则

所有 `path` 和 `new_path` 都必须是相对执行器当前所选项目根目录的路径。

示例：

```text
允许：README.md、docs/spec.md、src/main.py、70_后台文件/10_知识资产/素材.md
禁止：E:\Project\README.md、C:/Project/README.md、/Users/name/project/README.md、../README.md、docs/*.md
```

要求：

1. 不使用绝对路径、Windows 盘符、`..`、通配符或空路径；
2. 不得逃逸项目根目录，推荐统一使用 `/`；
3. `delete_dir`、`rename_dir`、`move_dir`、`copy_dir`、`create_dir` 不得作用于项目根目录；
4. 修改包不得重命名、移动、删除或改变执行器所选项目根目录本身；
5. 如需修改项目根目录名称，应先执行只作用于根目录内部文件的修改包，完成后由用户手动重命名；
6. 所有 `path` 和 `new_path` 必须按当前项目根目录计算，不得提前按未来目录名计算。

---

## 4. OP 分类与总览

### 4.1 OP 分类

- 路径类：`write_file`、`delete_file`、`delete_dir`、`rename_file`、`move_file`、`copy_file`、`rename_dir`、`move_dir`、`copy_dir`、`create_dir`
- 内容类：`append_text`、`replace_exact`、`replace_between`
- 目标类：`write_file`、`rename_file`、`move_file`、`copy_file`、`rename_dir`、`move_dir`、`copy_dir`，支持 `if_exists="error|overwrite|skip"`，默认 `error`

### 4.2 OP 总览

| OP | 用途 |
| --- | --- |
| `write_file` | 写入完整文件 |
| `append_text` | 向已有文件末尾追加文本 |
| `replace_exact` | 精确替换旧文本 |
| `replace_between` | 替换两个锚点之间的完整区间 |
| `delete_file` | 删除单个文件 |
| `delete_dir` | 删除整个文件夹 |
| `rename_file` | 同目录内重命名文件 |
| `move_file` | 移动文件，可跨目录，可改名 |
| `copy_file` | 复制文件 |
| `rename_dir` | 同目录内重命名文件夹 |
| `move_dir` | 移动文件夹，可跨目录，可改名 |
| `copy_dir` | 复制文件夹 |
| `create_dir` | 创建文件夹 |

### 4.3 OP 唯一标识 id

每个 OP 必须在 `---OP` 行声明 `id` 字段，用于执行器日志、失败提示和用户快速定位修改包中的具体 OP。

通用格式：

```text
---OP 操作类型 id="唯一OP标识" path="相对路径" 参数="..."
```

示例：

```text
---OP replace_exact id="op018" path="ui/favorites.py" count="1"
```

要求：

1. `id` 必填；
2. 同一修改包内所有 OP 的 `id` 必须唯一；
3. `id` 只能包含字母、数字、下划线和短横线；
4. `id` 不得为空；
5. `id` 建议长度为 3 到 80 个字符；
6. 推荐使用递增编号格式：`op001`、`op002`、`op003`；
7. 复杂修改包可使用编号加语义后缀，例如：`op018_fix_favorites_close_popup`；
8. `id` 仅用于日志、报错和人工定位，不改变 OP 执行语义；
9. 执行器校验或执行失败时，必须输出失败 OP 的 `id`；
10. AI 输出修改包前必须检查所有 OP 的 `id` 是否缺失、重复或格式非法。

执行失败时，定位提示应优先使用 `id`：

```text
18. [失败] id="op018" replace_exact path="ui/favorites.py"

失败原因：
replace_exact 命中次数不符：expected=1, actual=0

修改包定位：
请在修改包中搜索：
id="op018"
```

---

## 5. 全局互斥规则

### 5.1 路径互斥

各 OP 的相关路径：

| OP | 相关路径 |
| --- | --- |
| `write_file` / `delete_file` / `delete_dir` / `create_dir` / `append_text` / `replace_exact` / `replace_between` | `path` |
| `rename_file` / `move_file` / `copy_file` / `rename_dir` / `move_dir` / `copy_dir` | `path`, `new_path` |

路径互斥指两个相关路径不得完全相同，也不得形成父子包含关系。

同一修改包内，除两个内容类 OP 之间外，任意两个 OP 的相关路径必须路径互斥。

拥有 `path` 和 `new_path` 的 OP，其自身 `path` 与 `new_path` 也必须路径互斥。

禁止：

```text
---OP delete_file id="op001" path="a.md"
---END_OP
---OP copy_file id="op002" path="template.md" new_path="a.md"
---END_OP
```

如需覆盖，应使用单个目标类 OP：

```text
---OP copy_file id="op001" path="template.md" new_path="a.md" if_exists="overwrite"
---END_OP
```

### 5.2 内容互斥

内容互斥只适用于同一文件内的 `replace_exact` 和 `replace_between`。

规则：

1. `replace_exact` 与 `replace_between` 必须先解析为源文件中的旧文本位置区间；
2. 同一文件内任意两个旧文本位置区间不得重叠；
3. 比较的是旧文本在源文件中的实际位置区间，不是比较 OLD 字符串本身；
4. `append_text` 不参与内容互斥；
5. 多个 `append_text` 可以指向同一文件，按 OP 顺序追加；
6. 所有 OP 都应视为基于执行前的同一份当前文件内容校验；
7. 禁止依赖前序 OP 的结果让后续 OP 才能定位成功。

---

## 6. if_exists

目标类 OP 支持 `if_exists="error|overwrite|skip"`，默认 `error`。
`create_dir` 仅支持 `if_exists="error|skip"`，默认 `skip`。

含义：

| 值 | 含义 |
| --- | --- |
| `error` | 目标已存在则失败 |
| `overwrite` | 目标已存在则整体替换目标 |
| `skip` | 目标已存在且类型匹配时跳过本 OP |

约束：

1. `overwrite` 只能整体替换，禁止 merge；
2. 文件类 OP 只能覆盖文件，不能覆盖目录；
3. 目录类 OP 只能覆盖目录，不能覆盖文件；
4. 目录覆盖必须整体替换，禁止合并、增量覆盖或保留目标额外文件；
5. `skip` 只处理目标已存在且类型匹配的情况；
6. 源路径不存在、源类型不匹配、目标类型不匹配均失败。

---

## 7. OP 定义

### 7.1 通用正文规则

- 使用 `---CONTENT`：`write_file`、`append_text`、`replace_between`
- 使用 `---OLD` 和 `---NEW`：`replace_exact`
- 不允许正文块：`delete_file`、`delete_dir`、`rename_file`、`move_file`、`copy_file`、`rename_dir`、`move_dir`、`copy_dir`、`create_dir`

### 7.2 write_file

```text
---OP write_file id="op001" path="相对路径" if_exists="error"
---CONTENT
完整文件内容
AI_PATCH_BOUNDARY_...
---END_OP
```

规则：`path` 必填；`id` 必填且在同一修改包内唯一；目标不存在时新建文件；目标父目录不存在时自动创建；`if_exists` 使用目标类通用规则；目标存在且是目录时失败。

### 7.3 append_text

```text
---OP append_text id="op001" path="相对路径"
---CONTENT
追加内容
AI_PATCH_BOUNDARY_...
---END_OP
```

规则：`path` 必填；`id` 必填且在同一修改包内唯一；目标必须是已存在文件；固定追加到文件末尾。

### 7.4 replace_exact

```text
---OP replace_exact id="op001" path="相对路径" count="1"
---OLD
旧文本
AI_PATCH_BOUNDARY_...
---NEW
新文本
AI_PATCH_BOUNDARY_...
---END_OP
```

规则：

1. `path` 必填，目标必须是已存在文件；
2. `id` 必填且在同一修改包内唯一；
3. `count` 必填，且必须是大于等于 `1` 的整数；
4. `---OLD` 必须与目标文件当前文本精确匹配；
5. 实际命中次数必须等于 `count`；
6. `count > 1` 表示替换全部命中的多处旧文本；
7. 同文件多个 replace 类 OP 的旧文本位置区间不得重叠。

### 7.5 replace_between

```text
---OP replace_between id="op001" path="相对路径" start_marker="起始锚点" end_marker="结束锚点"
---CONTENT
新区间完整内容
AI_PATCH_BOUNDARY_...
---END_OP
```

规则：

1. `path` 必填，目标必须是已存在文件；
2. `id` 必填且在同一修改包内唯一；
3. `start_marker` 和 `end_marker` 必填，且在目标文件中都必须唯一；
4. 起始锚点必须位于结束锚点之前；
5. 替换区间固定包含 `start_marker` 和 `end_marker`；
6. `---CONTENT` 必须是替换后的完整区间内容；
7. 如需保留前后锚点，必须在 `---CONTENT` 中显式写回。

### 7.6 delete_file

```text
---OP delete_file id="op001" path="相对文件路径" reason="删除原因"
---END_OP
```

规则：`path` 必填；`id` 必填且在同一修改包内唯一；`reason` 可选，建议填写；目标必须是已存在文件；不允许删除目录。

### 7.7 delete_dir

```text
---OP delete_dir id="op001" path="相对目录路径" reason="删除原因"
---END_OP
```

规则：`path` 必填；`id` 必填且在同一修改包内唯一；`reason` 可选，建议填写；目标必须是已存在目录；不允许删除项目根目录；表示删除该目录本身及其全部子内容；不需要逐个列出子文件。

### 7.8 文件路径 OP

#### rename_file

```text
---OP rename_file id="op001" path="docs/old.md" new_path="docs/new.md" if_exists="error"
---END_OP
```

规则：`id` 必填且在同一修改包内唯一；`path` 必须存在且是文件；`path` 与 `new_path` 必须同父目录；`if_exists` 使用目标类通用规则。

#### move_file

```text
---OP move_file id="op001" path="draft/a.md" new_path="final/a.md" if_exists="error"
---END_OP
```

规则：`id` 必填且在同一修改包内唯一；`path` 必须存在且是文件；允许跨目录和改名；目标父目录不存在时自动创建；`if_exists` 使用目标类通用规则。

#### copy_file

```text
---OP copy_file id="op001" path="template.md" new_path="a.md" if_exists="error"
---END_OP
```

规则：`id` 必填且在同一修改包内唯一；`path` 必须存在且是文件；源文件不变；目标父目录不存在时自动创建；`if_exists` 使用目标类通用规则。

### 7.9 目录路径 OP

#### rename_dir

```text
---OP rename_dir id="op001" path="docs/old" new_path="docs/new" if_exists="error"
---END_OP
```

规则：`id` 必填且在同一修改包内唯一；`path` 必须存在且是目录；`path` 与 `new_path` 必须同父目录；不允许作用于项目根目录；`if_exists` 使用目标类通用规则。

#### move_dir

```text
---OP move_dir id="op001" path="old/topic" new_path="new/topic" if_exists="error"
---END_OP
```

规则：`id` 必填且在同一修改包内唯一；`path` 必须存在且是目录；不允许作用于项目根目录；允许跨目录和改名；目标父目录不存在时自动创建；`if_exists` 使用目标类通用规则。

#### copy_dir

```text
---OP copy_dir id="op001" path="template_project" new_path="project_a" if_exists="error"
---END_OP
```

规则：`id` 必填且在同一修改包内唯一；`path` 必须存在且是目录；源目录不变；不允许复制项目根目录；目标父目录不存在时自动创建；`if_exists` 使用目标类通用规则。

#### create_dir

```text
---OP create_dir id="op001" path="docs/new" if_exists="skip"
---END_OP
```

规则：`path` 必填；`id` 必填且在同一修改包内唯一；自动创建多级目录；不允许作用于项目根目录；`if_exists` 仅支持 `error|skip`，默认 `skip`；路径已存在但不是目录时失败。

---

## 8. 锚点与旧文本

使用 `replace_exact` 或 `replace_between` 时必须保证定位安全。

要求：

1. 不使用 `---`、空行、常见短句作为锚点；
2. 优先使用稳定标题、函数定义、类定义或唯一上下文；
3. `replace_exact` 的 `---OLD` 必须与执行前原文精确匹配；
4. `replace_between` 的起止锚点必须在执行前原文中唯一，结束锚点优先使用下一个同级标题或稳定结构标记；
5. 禁止依赖 OP 顺序副作用让后续 OP 定位成功；
6. 同一文件内多个替换 OP 的 OLD、起始锚点和结束锚点，都必须能在执行前当前文件中独立命中；
7. 连续改动相互影响的区域时，应合并为一个更大的 `replace_exact`、`replace_between`，或使用 `write_file`；
8. 如果定位不安全，应扩大上下文、改用 `write_file`，或要求补充原文。

---

## 9. 失败定位规则

执行器在校验或执行某个 OP 失败时，必须输出失败 OP 的 `id`，以便用户快速定位修改包中的具体条目。

要求：

1. 失败提示必须包含失败 OP 的 `id`；
2. 失败提示应包含 OP 类型和主要路径；
3. 定位说明应优先提示用户搜索 `id="..."`；
4. 不要求输出 OLD 片段、CONTENT 片段或正文摘要；
5. 不应依赖 OLD 首行、锚点文本或 OP 头同质内容作为主要定位方式。

推荐格式：

```text
18. [失败] id="op018" replace_exact path="ui/favorites.py"

失败原因：
replace_exact 命中次数不符：expected=1, actual=0

修改包定位：
请在修改包中搜索：
id="op018"
```

---

## 10. 输出前自检

输出 V2 修改包前必须检查：

| 检查项 | 要求 |
| --- | --- |
| 输出载体 | 整包放入 Markdown `text` 代码块，外层围栏足够长，不能溢出 |
| 协议 | 使用 `AI_FILE_PATCH_V2` |
| 包头/包尾 | 格式完整，包尾后无非空内容 |
| boundary | 足够长、唯一、正文中不单独成行出现，并包含 14 位时间码 |
| OP | 每个 OP 都有 `---END_OP` |
| OP id | 每个 OP 必须声明 `id` |
| id 唯一性 | 同一修改包内所有 `id` 不得重复 |
| id 格式 | `id` 只能包含字母、数字、下划线和短横线，且非空 |
| 正文块 | 每个正文块都由 boundary 单独成行结束 |
| path/new_path | 相对路径；不含盘符、`..`、通配符；不逃逸项目根目录；按当前项目根目录计算 |
| 项目根目录 | 不得通过修改包重命名、移动、删除或改变项目根目录本身 |
| 路径互斥 | 除两个内容类 OP 外，所有相关路径不得相同或父子包含 |
| 内容互斥 | 同文件 replace 类 OP 的旧文本位置区间不得重叠 |
| OP 独立校验 | 后续 OP 不得依赖本包内前序 OP 修改后的中间结果 |
| if_exists | 只使用该 OP 允许的值 |
| overwrite | 只表达整体替换，不表达 merge |
| replace_exact | 显式声明 count；OLD 基于执行前当前文件精确命中 |
| replace_between | 起止锚点基于执行前当前文件唯一命中且顺序正确；CONTENT 是包含锚点的完整新区间 |
| 删除 OP | 只在明确确认删除时使用 |
| 失败定位 | 执行器错误信息必须输出失败 OP 的 `id` |
| 备份路径 | 修改包不声明备份路径 |

---

## 11. 示例

```text
<<AI_FILE_PATCH_V2 boundary="AI_PATCH_BOUNDARY_EXAMPLE_20260713004354_A1B2C3D4E5F6">>
---OP copy_file id="op001" path="templates/readme.md" new_path="README.md" if_exists="skip"
---END_OP
---OP replace_exact id="op002" path="docs/spec.md" count="1"
---OLD
旧标题
AI_PATCH_BOUNDARY_EXAMPLE_20260713004354_A1B2C3D4E5F6
---NEW
新标题
AI_PATCH_BOUNDARY_EXAMPLE_20260713004354_A1B2C3D4E5F6
---END_OP
<</AI_FILE_PATCH_V2>>
```
