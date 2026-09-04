const IMAGE_RESOURCE_PATTERN = /\.(?:avif|bmp|gif|ico|jpe?g|png|svg|webp)$/i;

export function isImageResourcePath(path: string) {
  return IMAGE_RESOURCE_PATTERN.test(path);
}
