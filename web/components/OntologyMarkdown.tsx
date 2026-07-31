"use client";

import Markdown from "@/components/Markdown";

interface Props {
  narrativeMd: string;
}

const MARKER_RE = /\[\[.*?\]\]/g;

/** narrative_md 에서 [[...]] 마커를 제거한 순수 마크다운을 반환.
 *
 *  마커는 백엔드 _narrative_ontology_refs() 가 파싱해 ontology_refs(stage="narrative")에
 * 附加한다. 웹에서는 마커 대신 구조화 섹션의 InfoDot으로_same 정보를 확인할 수 있다.
 */
function stripMarkers(text: string): string {
  return text.replace(MARKER_RE, "");
}

export default function OntologyMarkdown({ narrativeMd }: Props) {
  if (!narrativeMd) return null;
  return <Markdown content={stripMarkers(narrativeMd)} />;
}
