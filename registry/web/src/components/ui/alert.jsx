import { cn } from "../../lib/utils";

export function Alert({ className, variant = "default", ...props }) {
  return <div className={cn("alert", `alert-${variant}`, className)} {...props} />;
}
