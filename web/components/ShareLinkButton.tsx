"use client";

import { useState } from "react";

import { createDeepDiveShare } from "@/lib/api";

import styles from "./ShareLinkButton.module.css";

// 딥다이브 보고서를 30분짜리 무인증 공유 링크로 굳혀 클립보드에 복사한다. 생성된 링크·만료 안내를 노출.
export default function ShareLinkButton({ code }: { code: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onShare = async () => {
    setBusy(true);
    setError(null);
    try {
      const { token } = await createDeepDiveShare(code);
      const link = `${window.location.origin}/share/${token}`;
      setUrl(link);
      try {
        await navigator.clipboard.writeText(link);
        setCopied(true);
      } catch {
        // 클립보드 권한 없거나 비보안 컨텍스트 — 링크는 화면에 노출되므로 수동 복사 가능.
        setCopied(false);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "공유 링크 생성 실패");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.wrap}>
      <button type="button" className={styles.shareBtn} onClick={onShare} disabled={busy}>
        {busy ? "생성 중…" : "🔗 공유 링크"}
      </button>
      {url ? (
        <div className={styles.result}>
          <span className={styles.note}>{copied ? "링크 복사됨 · 30분 후 만료" : "30분 후 만료"}</span>
          <a className={styles.link} href={url} target="_blank" rel="noreferrer">
            {url}
          </a>
        </div>
      ) : null}
      {error ? <span className={styles.error}>{error}</span> : null}
    </div>
  );
}
