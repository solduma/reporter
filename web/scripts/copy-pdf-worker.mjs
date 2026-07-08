// react-pdf(pdf.js)의 worker 파일을 public/ 으로 복사한다.
// 버전 드리프트를 막기 위해 설치/빌드 시 node_modules에서 그대로 가져온다.
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);

const pdfjsEntry = require.resolve("pdfjs-dist");
const pdfjsBuildDir = dirname(pdfjsEntry);
const workerSrc = join(pdfjsBuildDir, "pdf.worker.min.mjs");

const publicDir = join(process.cwd(), "public");
mkdirSync(publicDir, { recursive: true });
const workerDest = join(publicDir, "pdf.worker.min.mjs");

copyFileSync(workerSrc, workerDest);
console.log(`[copy-pdf-worker] ${workerSrc} -> ${workerDest}`);
