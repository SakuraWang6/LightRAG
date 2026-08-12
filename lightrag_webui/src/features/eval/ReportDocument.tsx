import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FileTextIcon } from 'lucide-react'

import type { EvalArtifact } from '@/api/eval'
import Badge from '@/components/ui/Badge'
import { Card, CardContent } from '@/components/ui/Card'
import EmptyCard from '@/components/ui/EmptyCard'
import MarkdownReport from '@/features/eval/MarkdownReport'
import { formatDate } from '@/features/eval/utils'

interface ReportDocumentProps {
  artifact: EvalArtifact
}

function headingText(children: React.ReactNode): string {
  if (Array.isArray(children)) return children.map(headingText).join('')
  if (typeof children === 'string' || typeof children === 'number') return String(children)
  return ''
}

function headingId(title: string): string {
  return `report-heading-${encodeURIComponent(title)}`
}

function makeHeading(level: number) {
  return function Heading(props: { children?: React.ReactNode }) {
    const Tag = `h${level}` as 'h1'
    return (
      <Tag id={headingId(headingText(props.children))} className="scroll-mt-20">
        {props.children}
      </Tag>
    )
  }
}

export default function ReportDocument({ artifact }: ReportDocumentProps) {
  const { t } = useTranslation()
  const [tocHover, setTocHover] = useState(false)

  const components = useMemo(
    () => ({
      h1: makeHeading(1),
      h2: makeHeading(2),
      h3: makeHeading(3),
      h4: makeHeading(4)
    }),
    []
  )

  if (!artifact.report_md) {
    return <EmptyCard title={t('eval.noReports')} description={t('eval.noReportsHint')} />
  }
  const toc = artifact.toc ?? []

  return (
    <div className="min-w-0 flex-1">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <FileTextIcon className="text-muted-foreground size-4" />
        <h2 className="text-base font-semibold">{artifact.title}</h2>
        <Badge variant="outline" className="text-muted-foreground text-[10px]">
          {t('eval.updatedAt')}: {formatDate(artifact.updated_at)}
        </Badge>
      </div>
      <Card className="relative">
        {toc.length > 0 ? (
          <div
            className="absolute top-3 left-3 z-20"
            onMouseEnter={() => setTocHover(true)}
            onMouseLeave={() => setTocHover(false)}
          >
            {tocHover ? (
              <div className="bg-card max-h-[70vh] w-64 overflow-auto rounded-lg border p-3 shadow-lg">
                <p className="text-muted-foreground mb-2 text-xs font-medium">{t('eval.toc')}</p>
                <ul className="space-y-1">
                  {toc.map((entry, index) => (
                    <li key={index}>
                      <button
                        type="button"
                        className="text-muted-foreground hover:text-foreground block w-full truncate rounded px-2 py-0.5 text-left text-xs transition-colors hover:bg-accent"
                        style={{ paddingLeft: `${0.5 + (entry.level - 1) * 0.75}rem` }}
                        title={entry.title}
                        onClick={() => {
                          document
                            .getElementById(headingId(entry.title))
                            ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                        }}
                      >
                        {entry.title}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <span className="text-muted-foreground/80 cursor-pointer rounded-md border border-dashed bg-muted/40 px-2 py-1 text-xs transition-colors hover:bg-muted/70 hover:text-foreground">
                {t('eval.toc')}
              </span>
            )}
          </div>
        ) : null}
        <CardContent className="p-5">
          <MarkdownReport content={artifact.report_md} components={components} />
        </CardContent>
      </Card>
    </div>
  )
}
