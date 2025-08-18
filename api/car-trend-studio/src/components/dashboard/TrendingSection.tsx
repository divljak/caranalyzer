import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { TrendingUp } from "lucide-react";
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
export const TrendingSection = () => {
  const trendingData = [{
    model: "Volkswagen Polo",
    status: "High engagement, 159 avg views",
    metric: "↗ Trending",
    isSuccess: true
  }, {
    model: "Renault Clio",
    status: "High engagement, 146 avg views",
    metric: "↗ Trending",
    isSuccess: true
  }, {
    model: "Skoda Octavia",
    status: "Fast seller, 6.8 days avg",
    metric: "🔥 Hot",
    isSuccess: false
  }];
  return <Card className="w-full">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          
          <CardTitle className="text-lg">Trending Up</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {trendingData.map((item, index) => <TrendingItem key={index} model={item.model} status={item.status} metric={item.metric} isSuccess={item.isSuccess} />)}
      </CardContent>
    </Card>;
};