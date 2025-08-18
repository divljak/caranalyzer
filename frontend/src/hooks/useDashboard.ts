import { useQuery } from '@tanstack/react-query';
import { getDashboardData, healthCheck, type DashboardFilters, type DashboardData } from '@/lib/api';

export const useDashboard = (filters: Partial<DashboardFilters> = {}) => {
  return useQuery({
    queryKey: ['dashboard', filters],
    queryFn: async () => {
      console.log('Making API call with filters:', filters);
      try {
        // First test health check
        const health = await healthCheck();
        console.log('Health check passed:', health);
        
        // Then get dashboard data
        const data = await getDashboardData(filters);
        console.log('Dashboard data received:', data);
        return data;
      } catch (error) {
        console.error('API call failed:', error);
        throw error;
      }
    },
    staleTime: 5 * 60 * 1000, // 5 minutes
    retry: 2,
  });
};

export type { DashboardData, DashboardFilters };