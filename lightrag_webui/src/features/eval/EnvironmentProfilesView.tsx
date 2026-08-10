import { useCallback, useEffect, useState } from 'react'
import { ArrowLeftIcon, ShieldCheckIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  getEnvironmentProfileVersion,
  listEnvironmentProfiles,
  type EnvironmentProfile
} from '@/api/eval'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'

interface EnvironmentProfilesViewProps {
  onBack: () => void
}

export default function EnvironmentProfilesView({ onBack }: EnvironmentProfilesViewProps) {
  const [profiles, setProfiles] = useState<EnvironmentProfile[]>([])
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null)

  const load = useCallback(async () => {
    try {
      setProfiles(await listEnvironmentProfiles())
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const inspect = async (profileId: string, version: number) => {
    try {
      setSelected(await getEnvironmentProfileVersion(profileId, version))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b px-4 py-3">
        <Button variant="ghost" size="icon" onClick={onBack} tooltip="返回运行与对比">
          <ArrowLeftIcon className="size-4" />
        </Button>
        <h2 className="flex items-center gap-2 text-lg font-semibold"><ShieldCheckIcon className="size-5" />评测环境</h2>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className="mx-auto grid max-w-5xl gap-4 lg:grid-cols-[1fr_1.1fr]">
          <div className="space-y-3">
            {profiles.map((profile) => (
              <Card key={profile.id}>
                <CardHeader className="pb-2"><CardTitle className="text-sm">{profile.name}</CardTitle></CardHeader>
                <CardContent className="flex flex-wrap gap-2">
                  {profile.versions.map((version) => (
                    <Button key={version.version} size="sm" variant="outline" onClick={() => void inspect(profile.id, version.version)}>
                      v{version.version}
                      <Badge variant="outline" className={version.status === 'published' ? 'ml-2 border-emerald-300 text-emerald-700' : 'ml-2'}>{version.status}</Badge>
                    </Button>
                  ))}
                </CardContent>
              </Card>
            ))}
            {profiles.length === 0 ? <p className="text-muted-foreground text-sm">尚无环境档案。</p> : null}
          </div>
          <Card className="h-fit">
            <CardHeader className="pb-2"><CardTitle className="text-sm">实际配置</CardTitle></CardHeader>
            <CardContent>
              {selected ? <pre className="max-h-[60vh] overflow-auto rounded-md bg-muted p-3 text-xs">{JSON.stringify(selected, null, 2)}</pre> : <p className="text-muted-foreground text-sm">选择一个版本以查看不可变配置。</p>}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
