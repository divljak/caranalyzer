import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { TrendingUp, AlertTriangle, Activity } from "lucide-react";
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
          cardClass: "bg-green-50 border-green-200 hover:shadow-lg",
          iconClass: "text-green-600",
          badgeClass: "bg-green-100 text-green-800 border-green-300",
          Icon: TrendingUp
        };
      case "market-analysis":
        return {
          cardClass: "bg-orange-50 border-orange-200 hover:shadow-md",
          iconClass: "text-orange-600",
          badgeClass: "bg-orange-100 text-orange-800 border-orange-300",
          Icon: AlertTriangle
        };
      case "rising-demand":
        return {
          cardClass: "bg-blue-50 border-blue-200 hover:shadow-md",
          iconClass: "text-blue-600", 
          badgeClass: "bg-blue-100 text-blue-800 border-blue-300",
          Icon: Activity
        };
      default:
        return {
          cardClass: "bg-gray-50 border-gray-200 hover:shadow-md",
          iconClass: "text-gray-600",
          badgeClass: "bg-gray-100 text-gray-800 border-gray-300",
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