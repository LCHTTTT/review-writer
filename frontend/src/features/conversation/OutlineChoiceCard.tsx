import { useUiText } from "../../i18n/useUiText";

/** A conversational decision, not a second toolbar or automatic submission. */
export function OutlineChoiceCard() {
  const { text } = useUiText();
  return <section className="conversation-outline-choices" aria-label={text("大纲选择", "Outline choices")}>
    <h4>{text("下一步：选择大纲", "Next: choose an outline")}</h4>
    <p>{text("你希望按主题与文献推荐结构，按时间或方法等主线组织，还是使用自己的大纲？", "Would you like a topic-based recommendation, an organization by chronology or method, or your own outline?")}</p>
    <p className="muted">{text("直接回复即可，例如“生成主题推荐大纲”“按时间顺序生成大纲”，或发来你的章节安排。", "Reply directly, for example ‘Generate a topic-based outline’ or ‘Generate a chronological outline’, or send your section plan.")}</p>
  </section>;
}
