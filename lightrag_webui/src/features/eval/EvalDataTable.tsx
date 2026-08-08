import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from '@/components/ui/Table'
import type { TableData } from '@/api/eval'
import { formatMetricCell } from '@/features/eval/utils'

interface EvalDataTableProps {
  data: TableData
  maxHeight?: string
}

export default function EvalDataTable({ data, maxHeight = 'max-h-[60vh]' }: EvalDataTableProps) {
  const columns = data.columns.length > 0
    ? data.columns
    : Object.keys(data.rows[0] ?? {}).map((key) => ({ key, label: key }))

  if (data.rows.length === 0) {
    return <p className="text-muted-foreground p-4 text-sm">No rows.</p>
  }

  return (
    <div className={`${maxHeight} overflow-auto rounded-md border`}>
      <Table className="min-w-full text-left text-sm">
        <TableHeader className="sticky top-0 bg-background">
          <TableRow>
            {columns.map((column) => (
              <TableHead key={column.key} className="whitespace-nowrap px-3 py-2">
                {column.label}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.rows.map((row, rowIndex) => (
            <TableRow key={rowIndex}>
              {columns.map((column) => {
                const value = row[column.key]
                const rendered = formatMetricCell(value)
                const long = typeof value === 'string' && value.length > 80
                return (
                  <TableCell
                    key={column.key}
                    className={`px-3 py-2 align-top ${long ? 'max-w-[420px]' : ''}`}
                    title={typeof value === 'string' ? value : undefined}
                  >
                    <span className={long ? 'line-clamp-3 whitespace-pre-wrap break-words' : 'whitespace-nowrap'}>
                      {rendered}
                    </span>
                  </TableCell>
                )
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
