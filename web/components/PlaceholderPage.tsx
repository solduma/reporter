import styles from "./PlaceholderPage.module.css";

interface Props {
  title: string;
  description: string;
}

export default function PlaceholderPage({ title, description }: Props) {
  return (
    <div className={styles.wrap}>
      <div className={styles.card}>
        <span className={styles.badge}>준비 중</span>
        <h1 className={styles.title}>{title}</h1>
        <p className={styles.desc}>{description}</p>
      </div>
    </div>
  );
}
