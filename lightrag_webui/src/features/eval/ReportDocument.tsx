import { useMemo, useRef } from 'react'
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

export default function ReportDocument({ artifact }: ReportDocumentProps) {
  const { t } = useTranslation()
  const counter = useRef(0)

  const heading = (level: number) =>
    function Heading(props: { children?: React.ReactNode }) {
      const index = counter.current++
      const Tag = `h${level}` as 'h1'
      return (
        <Tag id={`report-heading-${index}`} className="scroll-mt-20">
          {props.children}
        </Tag>
      )
    }

  const components = useMemo(
    () => ({
      h1: heading(1),
      h2: heading(2),
      h3: heading(3),
      h4: heading(4)
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  )

  if (!artifact.report_md) {
    return <EmptyCard title={t('eval.noReports')} description={t('eval.noReportsHint')} />
  }
  counter.current = 0

  const toc = artifact.toc ?? []

  return (
    <div className="flex gap-4">
      {toc.length > 0 ? (
        <nav className="sticky top-0 hidden max-h-[calc(100vh-8rem)] w-60 shrink-0 overflow-auto lg:block">
          <Card>
            <CardContent className="p-3">
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
                          .getElementById(`report-heading-${index}`)
                          ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                      }}
                    >
                      {entry.title}
                    </button>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </nav>
      ) : null}

      <div className="min-w-0 flex-1">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <FileTextIcon className="text-muted-foreground size-4" />
          <h2 className="text-base font-semibold">{artifact.title}</h2>
          <Badge variant="outline" className="text-muted-foreground text-[10px]">
            {t('eval.updatedAt')}: {formatDate(artifact.updated_at)}
          </Badge>
        </div>
        <Card>
          <CardContent className="p-5">
          <MarkdownReport content={artifact.report_md} components={components} />
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
