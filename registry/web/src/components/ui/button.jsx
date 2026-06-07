import { cn } from "../../lib/utils";

export function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}) {
  return (
    <button
      className={cn("button", `button-${variant}`, `button-${size}`, className)}
      {...props}
    />
  );
}
