# 术语与记号对照

用于统一讲解用词，并处理中外教材差异。第一列为课堂常用，第二列为英文教材常用。

## 极限与连续

| 中文 | English | 记号约定 |
| --- | --- | --- |
| 极限 | limit | $\lim_{x\to a} f(x)$ |
| 左极限 / 右极限 | left-hand / right-hand limit | $f(a^-)$、$f(a^+)$ |
| 无穷小 | infinitesimal | $\alpha(x)\to 0$ |
| 同阶/等价无穷小 | same order / equivalent infinitesimal | $f\sim g$ |
| 间断点 | discontinuity | 第一类（可去、跳跃）/ 第二类 |
| 连续 | continuous | $f\in C(a)$ |

## 导数与微分

| 中文 | English | 记号约定 |
| --- | --- | --- |
| 导数 | derivative | $f'(x)$ |
| 可导 | differentiable | — |
| 微分 | differential | $\mathrm{d}y = f'(x)\mathrm{d}x$ |
| 高阶导数 | higher-order derivative | $f^{(n)}(x)$ |
| 隐函数求导 | implicit differentiation | — |
| 参数方程求导 | parametric differentiation | $\mathrm{d}y/\mathrm{d}x = y'(t)/x'(t)$ |

## 积分

| 中文 | English | 记号约定 |
| --- | --- | --- |
| 原函数 | antiderivative | $F'(x)=f(x)$ |
| 不定积分 | indefinite integral | $\int f(x)\mathrm{d}x$ |
| 定积分 | definite integral | $\int_a^b f(x)\mathrm{d}x$ |
| 反常积分 | improper integral | — |
| 换元积分 | substitution | — |
| 分部积分 | integration by parts | $\int u\,\mathrm{d}v = uv-\int v\,\mathrm{d}u$ |

## 多变量

| 中文 | English | 记号约定 |
| --- | --- | --- |
| 偏导数 | partial derivative | $\partial f/\partial x$ |
| 全微分 | total differential | $\mathrm{d}f = f_x\mathrm{d}x+f_y\mathrm{d}y$ |
| 梯度 | gradient | $\nabla f$ |
| 雅可比行列式 | Jacobian | $\partial(x,y)/\partial(u,v)$ |
| 条件极值 | constrained extremum | 拉格朗日乘数 $\lambda$ |

## 中外差异提示

- 国外教材把「微分」讲成线性逼近，国内教材更强调 $\mathrm{d}y=f'(x)\mathrm{d}x$ 的形式运算。讲解时两种都给，考试按国内写法。
- 国外教材的「极限」多用 $\varepsilon$-$\delta$，国内课堂常先给直观定义。按用户考纲决定深度。
- 曲面积分的方向约定国内外一致，但参数化写法差异较大，以用户教材为准。
