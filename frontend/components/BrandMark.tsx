import Image from "next/image";

export function BrandMark() {
  return (
    <Image
      className="brand-mark"
      src="/brand/sarn-sap-mark.png"
      alt=""
      width={84}
      height={84}
      priority
      unoptimized
    />
  );
}
