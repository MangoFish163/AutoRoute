import { cn } from '../../lib/utils'

export function Badge({ children, tone = 'neutral', dot = false, className }) {
  return <span className={cn('badge', tone, className)}>{dot && <i />}{children}</span>
}
