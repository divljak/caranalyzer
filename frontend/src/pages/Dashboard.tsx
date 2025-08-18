import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { TrendingUp, DollarSign, Clock, BarChart3, Filter, RefreshCw } from "lucide-react";
import { FastestSellingTable } from "@/components/dashboard/FastestSellingTable";
import { useDashboard } from "@/hooks/useDashboard";
import { type DashboardFilters, refreshData, getLastUpdate } from "@/lib/api";
import { useState, useEffect } from "react";
import { toast } from "sonner";

const Dashboard = () => {
  const [filters, setFilters] = useState<Partial<DashboardFilters>>({
    timeframe_days: 60,
    max_price: 25000,
    price_min: 0,
    year_min: 2000,
    year_max: 2024,
    transmission: 'Any',
    min_listings: 5
  });

  const [lastUpdate, setLastUpdate] = useState<{last_update?: string, total_listings: number, new_today: number} | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const { data: dashboardData, isLoading, error, refetch } = useDashboard(filters);

  // Load last update info on component mount
  useEffect(() => {
    const loadLastUpdate = async () => {
      try {
        const updateInfo = await getLastUpdate();
        setLastUpdate(updateInfo);
      } catch (error) {
        console.error('Failed to load last update info:', error);
      }
    };
    loadLastUpdate();
  }, []);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      const response = await refreshData();
      toast.success(response.message);
      
      // Wait a moment then refresh the dashboard data and last update info
      setTimeout(async () => {
        await refetch();
        const updateInfo = await getLastUpdate();
        setLastUpdate(updateInfo);
        setIsRefreshing(false);
        toast.success('Dashboard updated with fresh data!');
      }, 3000);
      
    } catch (error) {
      console.error('Failed to refresh data:', error);
      toast.error('Failed to refresh data. Please try again.');
      setIsRefreshing(false);
    }
  };

  return (
    <div className="min-h-screen bg-background">
      <div className="container mx-auto px-6 py-6">
        {/* Essential Filters - Compact Row */}
        <Card className="mb-6">
          <CardHeader className="pb-4">
            <CardTitle className="flex items-center gap-2 text-lg">
              <Filter className="w-5 h-5" />
              Market Filters
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
              <div>
                <Label htmlFor="priceMin">Min Price (KM)</Label>
                <Input
                  id="priceMin"
                  type="number"
                  value={filters.price_min || 0}
                  onChange={(e) => setFilters({...filters, price_min: parseInt(e.target.value) || 0})}
                  placeholder="0"
                />
              </div>
              <div>
                <Label htmlFor="priceMax">Max Price (KM)</Label>
                <Input
                  id="priceMax"
                  type="number"
                  value={filters.max_price || 25000}
                  onChange={(e) => setFilters({...filters, max_price: parseInt(e.target.value) || 25000})}
                  placeholder="25000"
                />
              </div>
              <div>
                <Label htmlFor="timeframe">Analysis Period</Label>
                <div className="flex gap-2">
                  {[30, 60, 90].map((days) => (
                    <Button
                      key={days}
                      variant={filters.timeframe_days === days ? "default" : "outline"}
                      size="sm"
                      className="flex-1"
                      onClick={() => setFilters({...filters, timeframe_days: days})}
                    >
                      {days}d
                    </Button>
                  ))}
                </div>
              </div>
              <div>
                <Label htmlFor="yearRange">Year Range</Label>
                <Select 
                  value={`${filters.year_min || 2000}-${filters.year_max || 2024}`}
                  onValueChange={(value) => {
                    const [min, max] = value.split('-').map(Number);
                    setFilters({...filters, year_min: min, year_max: max});
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select year range" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="2020-2024">2020 - 2024 (Newest)</SelectItem>
                    <SelectItem value="2015-2024">2015 - 2024 (Modern)</SelectItem>
                    <SelectItem value="2010-2024">2010 - 2024 (Recent)</SelectItem>
                    <SelectItem value="2005-2024">2005 - 2024 (Reliable)</SelectItem>
                    <SelectItem value="2000-2024">2000 - 2024 (All)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label htmlFor="transmission">Transmission</Label>
                <Select 
                  value={filters.transmission || "Any"}
                  onValueChange={(value) => setFilters({...filters, transmission: value})}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Any transmission" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="Any">Any</SelectItem>
                    <SelectItem value="Manual">Manual</SelectItem>
                    <SelectItem value="Auto">Automatic</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Key Market Stats + Hot Opportunity */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          <Card>
            <CardContent className="p-6">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-muted-foreground">Total Listings</p>
                  <p className="text-2xl font-bold">{dashboardData?.stats?.total_listings?.toLocaleString() || "0"}</p>
                </div>
                <BarChart3 className="w-8 h-8 text-muted-foreground" />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-6">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-muted-foreground">Models Analyzed</p>
                  <p className="text-2xl font-bold">{dashboardData?.stats?.models_analyzed || "0"}</p>
                </div>
                <TrendingUp className="w-8 h-8 text-muted-foreground" />
              </div>
            </CardContent>
          </Card>

          {/* Hot Flip Opportunity */}
          {dashboardData?.opportunities?.find(o => o.type === "hot_flip") ? (
            <Card className="bg-gradient-to-r from-green-50 to-emerald-50 border-green-200">
              <CardContent className="p-6">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-green-600 font-medium">🔥 Hot Flip</p>
                    <p className="text-lg font-bold text-green-800">
                      {dashboardData.opportunities.find(o => o.type === "hot_flip")?.model || "No opportunity"}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-2xl font-bold text-green-800">
                      {dashboardData.opportunities.find(o => o.type === "hot_flip")?.days?.toFixed(0) || "0"} days
                    </p>
                    <p className="text-xs text-green-600">avg selling time</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          ) : (
            <Card className="bg-gray-50 border-gray-200">
              <CardContent className="p-6">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-muted-foreground">🔥 Hot Flip</p>
                    <p className="text-lg font-bold text-muted-foreground">No opportunity</p>
                  </div>
                  <div className="text-right">
                    <p className="text-2xl font-bold text-muted-foreground">0 days</p>
                    <p className="text-xs text-muted-foreground">avg selling time</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}
        </div>

        {/* Fastest Selling Models - Limited to Top 6 */}
        {dashboardData?.fastest_selling && (
          <FastestSellingTable 
            models={dashboardData.fastest_selling.slice(0, 6)} 
            filters={{
              price_min: filters.price_min,
              max_price: filters.max_price,
              transmission: filters.transmission
            }}
          />
        )}

        {/* Loading/Error States */}
        {isLoading && (
          <Card>
            <CardContent className="p-8 text-center">
              <div className="flex items-center justify-center gap-3">
                <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                <p className="text-lg">Loading market data...</p>
              </div>
            </CardContent>
          </Card>
        )}

        {error && (
          <Card className="border-red-200 bg-red-50">
            <CardContent className="p-6 text-center">
              <p className="text-red-700">
                ⚠️ Unable to load data. Please check your connection and try again.
              </p>
            </CardContent>
          </Card>
        )}

        {/* Refresh Data Section */}
        <Card className="mt-6">
          <CardContent className="p-6">
            <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
              <div className="flex flex-col items-center sm:items-start">
                <h3 className="text-lg font-semibold mb-2">Data Status</h3>
                {lastUpdate && (
                  <div className="text-sm text-muted-foreground space-y-1">
                    <div className="flex items-center gap-2">
                      <Clock className="w-4 h-4" />
                      <span>
                        Last updated: {lastUpdate.last_update 
                          ? new Date(lastUpdate.last_update).toLocaleString() 
                          : 'Never'}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <BarChart3 className="w-4 h-4" />
                      <span>Total listings: {lastUpdate.total_listings.toLocaleString()}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <TrendingUp className="w-4 h-4" />
                      <span>New today: {lastUpdate.new_today}</span>
                    </div>
                  </div>
                )}
              </div>
              
              <Button 
                onClick={handleRefresh} 
                disabled={isRefreshing}
                size="lg"
                className="flex items-center gap-2"
              >
                <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} />
                {isRefreshing ? 'Refreshing...' : 'Refresh Data'}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default Dashboard;