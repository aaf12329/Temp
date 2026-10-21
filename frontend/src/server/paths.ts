/** 服务端地址簿：Next进程工作目录 → 根.env默认位置；仅由服务器代理读取。 */
import { resolve } from "node:path";
export const PLATFORM_ENV = resolve(process.cwd(), "../.env");
