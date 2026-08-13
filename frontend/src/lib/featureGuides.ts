export type FeatureGuide = {
  path: string;
  navLabel: string;
  pageTitle: string;
  short: string;
  purpose: string;
  source: string;
  reading: string;
  caution: string;
  end?: boolean;
};

/**
 * 用户可见功能目录。导航和页内说明共用这一份定义，避免页面增加后只剩名称、没有解释。
 */
export const FEATURE_GUIDES: FeatureGuide[] = [
  {
    path: "/",
    navLabel: "总览",
    pageTitle: "研究日报",
    short: "今天先看什么",
    purpose: "用一屏回顾最近一份正式日报，并检查 Agent Session 是否发现需要你确认的新项目。",
    source: "以 Daily Report 原文为主；Agent Session、Git 和 Token 仅补充可核对的客观统计。",
    reading: "先读研究摘要和各项目结果，再看阻塞、明日计划与 Token；需要追溯时进入研究记录。",
    caution: "当天尚未生成正式日报时，会展示最近一份日报，不会把零散实时对话拼成当日结论。",
    end: true,
  },
  {
    path: "/records",
    navLabel: "研究记录",
    pageTitle: "研究记录",
    short: "按日期查原日报",
    purpose: "按日期或项目查阅每天实际记录的工作、动机、结果、关键文件、阻塞和计划。",
    source: "直接解析 Daily Report，并保留原文行号；客观统计来自对应日期的 Agent 采集结果。",
    reading: "适合回答“某天具体做了什么”或“这个项目过去有哪些记录”，也是其他语义页面的事实入口。",
    caution: "日报里没有写出的结论不会自动补成事实；reported、inferred 与 observed 应区别看待。",
  },
  {
    path: "/development",
    navLabel: "项目发展",
    pageTitle: "项目发展",
    short: "看项目如何演进",
    purpose: "把多日日报画成项目路线、工作类型、投入节奏、指标记录点和历史快照。",
    source: "来自正式日报事项及其日期；Token 来自归属到项目的 Agent Session 日增量。",
    reading: "选择一个项目，先看最近结果与阻塞，再沿时间查看它从探索、实现到验证的变化。",
    caution: "连线表示时间先后，不代表因果关系；日报里的不同指标口径也不会被强行连成趋势。",
  },
  {
    path: "/intelligence",
    navLabel: "项目情报",
    pageTitle: "项目情报",
    short: "变化、未知与投入产出",
    purpose: "在 30 秒内回答项目现在怎样、最近变了什么、还有哪些未知，以及投入是否换来有效进展。",
    source: "自然语言来自 Daily Report 及 Codex 审计缓存；Token 只作为投入量级，Git 只作为证据。",
    reading: "依次看 Project Pulse、Since Last Visit、Open Unknowns、投入与进展、突破时间线和项目故事。",
    caution: "Storyline 是有引用约束的压缩总结，不替代原日报；切换项目时不会临时调用模型。",
  },
  {
    path: "/architecture",
    navLabel: "算法架构",
    pageTitle: "算法架构",
    short: "模型与算法数据流",
    purpose: "解释项目用了什么模型、数据怎样流动、各模块为何存在、当前方案如何演化。",
    source: "综合源码、配置、评测、正式日报和经审阅的官方模型资料，证据变化后由 Codex 增量分析。",
    reading: "先看算法数据流，再点模型查看内部结构；设计决策、备选方案和待补证据分别阅读。",
    caution: "公开资料只说明模型家族，不等于本地部署已经验证；未披露的闭源结构会明确保持未知。",
  },
  {
    path: "/experiments",
    navLabel: "实验记录",
    pageTitle: "实验记录",
    short: "方法、指标与结论",
    purpose: "把日报中的训练、评测、消融和部署验证整理成可读实验卡，保留问题、方法、结果与结论。",
    source: "Daily Report 是语义主线；Agent Session、commit、文件和 Token 只用于补充归属与证据。",
    reading: "按项目筛选，先读结论和验证范围，再核对模型、数据集、参数、指标及原日报引用。",
    caution: "只有同项目、同指标、同单位、同口径才画趋势；Token 是项目当日共享增量，不是单实验精确成本。",
  },
  {
    path: "/analytics",
    navLabel: "数据分析",
    pageTitle: "数据分析",
    short: "Token 与产出趋势",
    purpose: "查看每天和各项目的 Agent Token、工作记录、实验与结论数量如何变化。",
    source: "Token 来自 Codex/Claude Session 累计计数器的逐日差分；产出数量来自正式日报提取。",
    reading: "把投入曲线和产出曲线一起看，用来发现异常波动或长期投入，而不是比较单日高低。",
    caution: "Token 包含缓存输入且可能跨项目共享；Token 多不代表质量高，也不等同于人工工作时间。",
  },
  {
    path: "/knowledge",
    navLabel: "结论与知识",
    pageTitle: "结论与知识",
    short: "汇总可复用认知",
    purpose: "把散落在多日日报和实验中的结论、决策与可复用经验集中到项目知识视图。",
    source: "来自正式日报、实验记录和经审计的 Agent 交接；每项尽量保留日期、项目、范围和证据。",
    reading: "先按项目筛选，再关注结论的适用范围、当前状态和来源，必要时返回对应日报核对。",
    caution: "项目特定经验不能自动当成通用规律；没有范围或证据的内容可信度更低。",
  },
  {
    path: "/radar",
    navLabel: "研究雷达",
    pageTitle: "研究雷达",
    short: "筛选值得读的论文",
    purpose: "根据你当前项目筛选近期论文，并用中文摘要、关键点和阅读价值帮助快速决定是否精读。",
    source: "论文元数据和摘要来自公开学术索引；本地项目关联与中文导读由缓存分析生成。",
    reading: "优先看 A/B 级、质量理由和阅读风险；真正相关时再展开英文摘要或打开论文原文。",
    caution: "等级表示当前项目下的推荐优先级，不是论文绝对质量；只有标题时生成的导读信息有限。",
  },
  {
    path: "/reports",
    navLabel: "日报与周览",
    pageTitle: "日报归档",
    short: "浏览历史正式记录",
    purpose: "按日期回看已经生成的完整日报，作为项目复盘、周览和历史追溯的稳定入口。",
    source: "直接读取 Daily Report 归档文件，不根据当前零散事件重新编写历史。",
    reading: "选择日期后查看当天摘要、项目记录、阻塞、计划闭环、知识和 Token 附录。",
    caution: "历史日报反映当时掌握的信息；后来被推翻的结论应结合较新的日报和知识页阅读。",
  },
];

export function featureGuideForTitle(title: string): FeatureGuide | undefined {
  return FEATURE_GUIDES.find((item) => item.pageTitle === title);
}
