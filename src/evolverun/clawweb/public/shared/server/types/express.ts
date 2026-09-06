import "express-serve-static-core";

declare module "express-serve-static-core" {
  interface Request {
    isAdmin?: boolean;
    isLogAdmin?: boolean;
    isBenchAdmin?: boolean;
    isClawEvolveAdmin?: boolean;
    isSuperAdmin?: boolean;
  }
}
