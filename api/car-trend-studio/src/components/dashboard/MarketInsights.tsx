import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";

interface PriceBracketData {
  title: string;
  hasData: boolean;
  data?: Array<{ model: string; days: number }>;
}

export const MarketInsights = () => {
  const overTwentyKData = [
    { model: "Skoda Octavia", days: 7 },
    { model: "Toyota Yaris", days: 8 },
    { model: "BMW X1", days: 8 }
  ];

  const priceBrackets: PriceBracketData[] = [
    { title: "Under 10K", hasData: false },
    { title: "10K - 20K", hasData: false },
    { title: "20K - 30K", hasData: true, data: overTwentyKData },
    { title: "30K - 40K", hasData: false },
    { title: "40K - 50K", hasData: false },
    { title: "50K - 60K", hasData: false }
  ];

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">Market Insights by Price Bracket</h2>
      <Card>
        <CardContent className="p-0">
          <Accordion type="multiple" className="w-full">
            {priceBrackets.map((bracket, index) => (
              <AccordionItem key={index} value={`item-${index}`} className="border-b last:border-b-0">
                <AccordionTrigger className="px-6 py-4 hover:no-underline">
                  <div className="flex items-center justify-between w-full mr-4">
                    <span className="text-sm font-medium">{bracket.title}</span>
                    {!bracket.hasData && (
                      <span className="text-xs text-muted-foreground">No sufficient data</span>
                    )}
                    {bracket.hasData && (
                      <span className="text-xs text-muted-foreground">
                        {bracket.data?.length} models
                      </span>
                    )}
                  </div>
                </AccordionTrigger>
                <AccordionContent className="px-6 pb-4">
                  {!bracket.hasData ? (
                    <div className="flex items-center justify-center py-8">
                      <p className="text-sm text-muted-foreground">No sufficient data available for this price bracket</p>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {bracket.data?.map((item, itemIndex) => (
                        <div key={itemIndex} className="flex justify-between items-center py-2 px-3 rounded-lg bg-secondary/50">
                          <span className="text-sm font-medium">{item.model}</span>
                          <span className="text-sm text-muted-foreground">{item.days} days</span>
                        </div>
                      ))}
                    </div>
                  )}
                </AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        </CardContent>
      </Card>
    </div>
  );
};