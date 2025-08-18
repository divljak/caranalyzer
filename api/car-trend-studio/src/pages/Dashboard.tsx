import { FiltersPanel } from "@/components/dashboard/FiltersPanel";
import { OpportunityCard } from "@/components/dashboard/OpportunityCard";
import { MarketInsights } from "@/components/dashboard/MarketInsights";
import { QuadrantChart } from "@/components/dashboard/QuadrantChart";
import { TrendingSection } from "@/components/dashboard/TrendingSection";
import { FastestSellingTable } from "@/components/dashboard/FastestSellingTable";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Car, Search, Bell, User, BarChart3, TrendingUp, Filter, Settings } from "lucide-react";

const Dashboard = () => {
  return (
    <div className="flex h-screen bg-background">

      <div className="flex-1 flex flex-col">

        <div className="flex-1 overflow-auto">
          <div className="p-6">
            <div className="flex gap-6">
              {/* Left Sidebar - Filters */}
              <FiltersPanel />

              {/* Main Content */}
              <div className="flex-1 space-y-6">

            {/* Top Opportunities */}
            <div className="space-y-4">
              <h2 className="text-xl font-semibold">Top Opportunities</h2>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <OpportunityCard
                  type="hot-flip"
                  title="Hot Flip: Skoda Octavia"
                  subtitle="Sells 23 days faster than average"
                  value="23 days"
                  badge="🔥 Hot"
                />
                <OpportunityCard
                  type="market-analysis"
                  title="Market Analysis"
                  subtitle="No major overpricing detected in current filters"
                  value="All Clear"
                />
                <OpportunityCard
                  type="rising-demand"
                  title="Rising Demand: Monitoring"
                  subtitle="Analyzing trends for emerging opportunities"
                  value="3 Models"
                />
              </div>
            </div>

            {/* Market Insights */}
            <MarketInsights />

            {/* Fastest Selling Table */}
            <FastestSellingTable />

            {/* Charts Section */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <div className="lg:col-span-2">
                <QuadrantChart />
              </div>
              <TrendingSection />
            </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;