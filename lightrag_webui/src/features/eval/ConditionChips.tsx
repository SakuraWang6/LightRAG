import type { RunCondition } from '@/api/eval'
import Badge from '@/components/ui/Badge'

interface ConditionChipsProps {
  conditions: RunCondition[]
  limit?: number
}

export default function ConditionChips({ conditions, limit = 6 }: ConditionChipsProps) {
  const visible = conditions.slice(0, limit)
  if (visible.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {visible.map((condition) => (
        <Badge key={condition.key} variant="secondary" className="text-muted-foreground max-w-full font-normal">
          <span className="mr-1 shrink-0 text-[10px] opacity-70">{condition.label}</span>
          <span className="truncate font-medium" title={condition.value}>
            {condition.value}
          </span>
        </Badge>
      ))}
      {conditions.length > limit ? (
        <Badge variant="outline" className="text-muted-foreground text-[10px]">
          +{conditions.length - limit}
        </Badge>
      ) : null}
    </div>
  )
}
