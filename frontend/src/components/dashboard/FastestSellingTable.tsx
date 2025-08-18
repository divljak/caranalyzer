import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ExternalLink, TrendingUp } from "lucide-react";
import { type FastestSellingModel } from "@/lib/api";

interface DemandBadgeProps {
  level: string;
}

const DemandBadge = ({ level }: DemandBadgeProps) => {
  const getBadgeProps = (level: string) => {
    switch (level) {
      case 'High':
        return { 
          className: 'demand-badge-high', 
          icon: '🔥',
          dot: 'bg-green-400'
        };
      case 'Medium':
        return { 
          className: 'demand-badge-medium', 
          icon: '⚡',
          dot: 'bg-yellow-400'
        };
      case 'Low':
        return { 
          className: 'demand-badge-low', 
          icon: '📈',
          dot: 'bg-slate-400'
        };
      default:
        return { 
          className: 'demand-badge-low', 
          icon: '❓',
          dot: 'bg-gray-400'
        };
    }
  };

  const { className, icon, dot } = getBadgeProps(level);

  return (
    <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium ${className}`}>
      <div className={`w-2 h-2 rounded-full ${dot} animate-pulse`} />
      <span>{icon}</span>
      <span className="font-semibold">{level}</span>
    </div>
  );
};

interface FastestSellingTableProps {
  models: FastestSellingModel[];
  filters?: {
    price_min?: number;
    max_price?: number;
    transmission?: string;
  };
}

export const FastestSellingTable = ({ models, filters }: FastestSellingTableProps) => {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <h2 className="text-xl font-semibold">🎯 Best Opportunities to Buy & Flip</h2>
        <p className="text-sm text-muted-foreground">
          Models selling fastest - ideal for quick turnaround and profit.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <TrendingUp className="h-5 w-5 text-success" />
            Market Leaders
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
            {models && models.length > 0 ? (
              models.map((model, index) => (
                <div
                  key={index}
                  className="model-card group p-6"
                  style={{ animationDelay: `${index * 100}ms` }}
                  onClick={() => {
                    // Build proper OLX.ba search URL
                    // The model field already contains "Make Model" (e.g., "BMW 118d")
                    const searchQuery = encodeURIComponent(model.model);
                    
                    // Base URL with car category (18 = automobiles)
                    let searchUrl = `https://olx.ba/pretraga?category_id=18&trazilica=${searchQuery}`;
                    
                    // Add year filter if available
                    if (model.year) {
                      searchUrl += `&year_from=${model.year}&year_to=${model.year}`;
                    }
                    
                    // Use broader price range for OLX search to ensure results
                    // (Our dashboard data might be different from current OLX listings)
                    if (filters?.price_min && filters.price_min > 0) {
                      // Reduce minimum by 20% to catch more results
                      const broadMinPrice = Math.floor(filters.price_min * 0.8);
                      searchUrl += `&price_from=${broadMinPrice}`;
                    }
                    if (filters?.max_price) {
                      // Increase maximum by 20% to catch more results  
                      const broadMaxPrice = Math.floor(filters.max_price * 1.2);
                      searchUrl += `&price_to=${broadMaxPrice}`;
                    }
                    
                    window.open(searchUrl, '_blank');
                  }}
                >
                  {/* Ranking Badge - Top Left */}
                  <div className="absolute top-4 left-4">
                    <div className={`
                      flex items-center justify-center w-8 h-8 rounded-full text-xs font-bold
                      ${index === 0 ? 'ranking-badge-1' : 
                        index === 1 ? 'ranking-badge-2' :
                        index === 2 ? 'ranking-badge-3' :
                        'ranking-badge-default'}
                    `}>
                      #{index + 1}
                    </div>
                  </div>

                  {/* External Link - Top Right */}
                  <div className="absolute top-4 right-4 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
                    <div className="h-8 w-8 rounded-full bg-primary/10 flex items-center justify-center">
                      <ExternalLink className="h-4 w-4 text-primary" />
                    </div>
                  </div>

                  {/* Main Content */}
                  <div className="mt-8 space-y-4">
                    {/* Model Name & Year */}
                    <div className="space-y-1">
                      <h4 className="text-lg font-semibold text-foreground group-hover:text-primary transition-colors">
                        {model.model}
                      </h4>
                      <p className="text-sm text-muted-foreground font-medium">
                        {model.year} Model Year
                      </p>
                    </div>

                    {/* Key Metrics Row */}
                    <div className="flex items-center justify-between">
                      <div className="flex flex-col space-y-1">
                        <span className="text-xs text-muted-foreground uppercase tracking-wide font-medium">
                          Average Days
                        </span>
                        <div className="flex items-center gap-2">
                          <span className="text-2xl font-bold text-foreground">
                            {model.avg_days_on_market.toFixed(0)}
                          </span>
                          <span className="text-sm text-muted-foreground">days</span>
                        </div>
                      </div>

                      <div className="flex flex-col items-end space-y-1">
                        <span className="text-xs text-muted-foreground uppercase tracking-wide font-medium">
                          Average Price
                        </span>
                        <span className="text-lg font-bold text-primary">
                          {model.avg_price.toLocaleString()} KM
                        </span>
                      </div>
                    </div>

                    {/* Demand Level Badge */}
                    <div className="flex justify-between items-center pt-2 border-t border-border/50">
                      <span className="text-xs text-muted-foreground uppercase tracking-wide font-medium">
                        Demand Level
                      </span>
                      <DemandBadge level={model.demand_level} />
                    </div>

                    {/* Search Link */}
                    <div className="pt-2">
                      <div className="flex items-center gap-2 text-xs text-muted-foreground group-hover:text-primary transition-colors">
                        <ExternalLink className="h-3 w-3" />
                        <span>Click to search on OLX.ba</span>
                      </div>
                    </div>
                  </div>

                  {/* Subtle Pattern Overlay */}
                  <div className="model-card-overlay" />
                </div>
              ))
            ) : (
              <div className="flex items-center justify-center py-8">
                <p className="text-sm text-muted-foreground">
                  No model data available with current filters.
                </p>
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default FastestSellingTable;