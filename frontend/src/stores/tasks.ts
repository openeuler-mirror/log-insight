/* 解析任务 store: 任务缓存 (详情页轮询/面包屑) + 团队任务分页 (服务端筛选 + spark 聚合) */
import { defineStore } from 'pinia'
import { get, del } from '@/api/http'

export interface AnalyzeTask {
  id: string
  type: string
  status: string              // pending / running / done / failed / cancelled / interrupted
  filename: string
  progress: number
  error: string
  createdAt: string
  finishedAt?: string | null
  name: string
  parserType: string
  teamId?: number | null
  logSize: number
  eventCount: number
  creatorName: string
  teamName: string
}

export interface TaskListPage {
  total: number
  items: AnalyzeTask[]
  sparks: Record<string, Record<string, any>[]>   // taskId -> 16 桶 [{t,label,total,critical,error,...}]
}

export interface TaskListQuery {
  statusFilter?: string
  parserType?: string
  kw?: string
  page?: number
  pageSize?: number
}

export const useTasksStore = defineStore('tasks', {
  state: () => ({
    tasks: {} as Record<string, AnalyzeTask>,           // 任务缓存 (详情页轮询/面包屑用)
    lastPage: null as TaskListPage | null,              // 最近一次团队任务分页结果
    loading: false,
  }),
  getters: {
    taskCached(state) {
      return (taskId: string | number) => state.tasks[String(taskId)]
    },
  },
  actions: {
    /* 团队任务分页 (服务端筛选 + spark 聚合) */
    async fetchTeamTasks(teamId: string | number, q: TaskListQuery = {}) {
      this.loading = true
      try {
        const page = await get<TaskListPage>(`/teams/${teamId}/tasks`, {
          status_filter: q.statusFilter || undefined,
          parser_type: q.parserType || undefined,
          kw: q.kw || undefined,
          page: q.page ?? 1,
          page_size: q.pageSize ?? 10,
        })
        this.lastPage = page
        for (const t of page.items) this.tasks[t.id] = t
        return page
      } finally {
        this.loading = false
      }
    },
    /* 任务详情 (含可见性校验, 403 时抛错) */
    async fetchTask(taskId: string) {
      const t = await get<AnalyzeTask>(`/tasks/${taskId}`)
      this.tasks[taskId] = t
      return t
    },
    async removeTask(taskId: string) {
      await del(`/tasks/${taskId}`)
      delete this.tasks[taskId]
    },
  },
})