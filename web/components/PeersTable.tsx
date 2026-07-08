import type { Peer } from "@/lib/types";

import styles from "./PeersTable.module.css";

interface Props {
  peers: Peer[];
  baseCode: string;
}

interface Column {
  key: keyof Omit<Peer, "stock_code" | "name">;
  label: string;
}

const COLUMNS: Column[] = [
  { key: "price", label: "현재가" },
  { key: "market_cap", label: "시가총액" },
  { key: "foreign_ratio", label: "외국인비율" },
  { key: "per", label: "PER" },
  { key: "pbr", label: "PBR" },
  { key: "roe", label: "ROE" },
];

export default function PeersTable({ peers, baseCode }: Props) {
  return (
    <div className={styles.scroll}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th className={styles.nameCol} scope="col">
              종목
            </th>
            {COLUMNS.map((col) => (
              <th key={col.key} scope="col">
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {peers.map((peer) => {
            const isBase = peer.stock_code === baseCode;
            return (
              <tr key={peer.stock_code} className={isBase ? styles.base : undefined}>
                <th className={styles.nameCol} scope="row">
                  <span className={styles.name}>{peer.name}</span>
                  <span className={styles.code}>{peer.stock_code}</span>
                </th>
                {COLUMNS.map((col) => (
                  <td key={col.key}>{peer[col.key] ?? "—"}</td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
