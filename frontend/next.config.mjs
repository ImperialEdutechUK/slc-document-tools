/** @type {import('next').NextConfig} */
// NEXT_OUTPUT=export builds a static site for S3 + CloudFront (AWS deploy).
// Without it the build is unchanged, so the Vercel/Railway deployment is unaffected.
const nextConfig =
  process.env.NEXT_OUTPUT === "export"
    ? { output: "export", images: { unoptimized: true } }
    : {};

export default nextConfig;
