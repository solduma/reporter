import type { Report } from "@/lib/types";

import ReportCard from "./ReportCard";
import styles from "./ReportColumn.module.css";

interface Props {
  title: string;
  icon: string;
  reports: Report[];
  loading: boolean;
}

export default function ReportColumn({ title, icon, reports, loading }: Props) {
  return (
    <div className={styles.column}>
      <div className={styles.header}>
        <span className={styles.icon}>{icon}</span>
        <h2 className={styles.title}>{title}</h2>
        {!loading ? <span className={styles.count}>{reports.length}</span> : null}
      </div>

      {loading ? (
        <p className={styles.placeholder}>불러오는 중…</p>
      ) : reports.length === 0 ? (
        <p className={styles.placeholder}>오늘 발행된 리포트가 없습니다</p>
      ) : (
        <div className={styles.list}>
          {reports.map((report) => (
            <ReportCard key={report.id} report={report} />
          ))}
        </div>
      )}
    </div>
  );
}
