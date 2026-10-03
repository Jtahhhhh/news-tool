import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, post } from "../api/client";
export function useApi<T>(path: string, interval = 20000) {
  return useQuery({
    queryKey: [path],
    queryFn: () => api<T>(path),
    refetchInterval: interval,
  });
}
export function useAction() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) =>
      post(path, body),
    onSuccess: () => {
      toast.success("Đã cập nhật");
      client.invalidateQueries();
    },
    onError: (e) => toast.error(e.message),
  });
}
