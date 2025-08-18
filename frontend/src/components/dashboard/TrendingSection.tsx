import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { TrendingUp } from "lucide-react";
import { type TrendingModel } from "@/lib/api";
interface TrendingItemProps {
  model: string;
  status: string;
  metric: string;
  isSuccess?: boolean;
}
const TrendingItem = ({
  model,
  status,
  metric,
  isSuccess
}: TrendingItemProps) => <div className="flex items-center justify-between p-3 rounded-lg bg-secondary hover:bg-secondary/80 transition-colors">
    <div className="space-y-1">
      <h4 className="font-medium text-base">{model}</h4>
      <p className="text-xs text-muted-foreground">{status}</p>
    </div>
    <Badge variant={isSuccess ? "default" : "secondary"} className="text-xs">
      {metric}
    </Badge>
  </div>;
interface TrendingSectionProps {
  trendingModels?: TrendingModel[];
}

export const TrendingSection = ({ trendingModels }: TrendingSectionProps) => {
  const defaultData = [{
    model: "Volkswagen Polo",
    trend_text: "High engagement, 159 avg views",
    trend_type: "views_up"
  }, {
    model: "Renault Clio",
    trend_text: "High engagement, 146 avg views",
    trend_type: "views_up"
  }, {
    model: "Skoda Octavia",
    trend_text: "Fast seller, 6.8 days avg",
    trend_type: "days_down"
  }];
  
  const trendingData = trendingModels && trendingModels.length > 0 ? trendingModels : defaultData;
  return <Card className="w-full">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          
          <CardTitle className="text-lg">Trending Up</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {trendingData.map((item, index) => {
          const getMetricFromType = (type: string) => {
            switch (type) {
              case 'views_up': return { metric: '↗ Trending', isSuccess: true };
              case 'days_down': return { metric: '🔥 Hot', isSuccess: false };
              default: return { metric: '📊 Analysis', isSuccess: true };
            }
          };
          const { metric, isSuccess } = getMetricFromType(item.trend_type);
          return (
            <TrendingItem 
              key={index} 
              model={item.model} 
              status={'trend_text' in item ? item.trend_text : item.status} 
              metric={metric} 
              isSuccess={isSuccess} 
            />
          );
        })}
      </CardContent>
    </Card>;
};