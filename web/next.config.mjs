/** @type {import('next').NextConfig} */

// 브라우저가 same-origin /api/... 를 호출하면 Next 서버가 loopback FastAPI 로 프록시한다.
// 배열 형태 rewrites 는 afterFiles 단계라 Next 소유 라우트(/api/login, /api/logout)가
// 먼저 매칭되고, 나머지 /api/* 만 FastAPI 로 넘어간다.
const API_PROXY_TARGET = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8010";

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_PROXY_TARGET}/api/:path*`,
      },
    ];
  },
  webpack: (config) => {
    // react-pdf(pdf.js)가 참조하는 선택적 native 모듈을 브라우저 번들에서 제외
    config.resolve.alias = {
      ...config.resolve.alias,
      canvas: false,
    };
    return config;
  },
};

export default nextConfig;
