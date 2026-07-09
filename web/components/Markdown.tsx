import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

import styles from "./Markdown.module.css";

interface Props {
  content: string;
  className?: string;
}

// LLM이 생성한 본문/요약은 **굵게**·목록·헤더 등 마크다운이 섞이고
// 단일 줄바꿈에도 의미가 있어 remark-breaks로 <br> 보존이 필요하다.
// 원격 콘텐츠이므로 raw HTML은 렌더하지 않는다(react-markdown 기본값).
export default function Markdown({ content, className }: Props) {
  const classes = className ? `${styles.markdown} ${className}` : styles.markdown;
  return (
    <div className={classes}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        components={{
          a: ({ node: _node, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer" />
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
