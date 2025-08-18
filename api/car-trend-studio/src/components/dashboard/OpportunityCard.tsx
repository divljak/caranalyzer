import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { TrendingUp, AlertTriangle, Activity, ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/utils";

interface OpportunityCardProps {
  type: "hot-flip" | "market-analysis" | "rising-demand";
  title: string;
  subtitle: string;
  metric?: string;
  value?: string;
  badge?: string;
}

export const OpportunityCard = ({ type, title, subtitle, metric, value, badge }: OpportunityCardProps) => {
  const getCardStyles = () => {
    switch (type) {
      case "hot-flip":
        return {
          cardClass: "bg-card-dark text-card-dark-foreground hover:shadow-lg",
          iconClass: "text-accent",
          badgeClass: "bg-accent text-accent-foreground",
          Icon: TrendingUp
        };
      case "market-analysis":
        return {
          cardClass: "bg-card hover:shadow-md border",
          iconClass: "text-muted-foreground",
          badgeClass: "bg-muted text-muted-foreground",
          Icon: AlertTriangle
        };
      case "rising-demand":
        return {
          cardClass: "bg-card hover:shadow-md border",
          iconClass: "text-muted-foreground", 
          badgeClass: "bg-muted text-muted-foreground",
          Icon: Activity
        };
    }
  };

  const { cardClass, iconClass, badgeClass, Icon } = getCardStyles();

  return (
    <Card className={cn("transition-all duration-200 cursor-pointer", cardClass)}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <Icon className={cn("h-5 w-5", iconClass)} />
          {badge && (
            <Badge className={cn("text-xs font-medium", badgeClass)}>
              {badge}
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="space-y-2">
          <h3 className="font-semibold text-lg leading-tight">{title}</h3>
          {value && (
            <div className="text-2xl font-bold">
              {value}
            </div>
          )}
          {metric && (
            <p className="text-sm opacity-75">{metric}</p>
          )}
          <p className="text-sm opacity-75 leading-relaxed">{subtitle}</p>
        </div>
      </CardContent>
    </Card>
  );
};